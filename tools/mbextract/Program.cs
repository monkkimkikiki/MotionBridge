// MotionBridge 추출 도우미 (CUE4Parse 기반).
//   mbextract list-games   (지원하는 UE 버전/게임 프리셋 이름 전체를 JSON 배열로)
//   mbextract probe  --game DIR --ue X [--aes] [--usmap]   (열어 보고 왜 안 되는지 코드로 알려 줌)
//   mbextract list   --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] [--filter REGEX] [--limit N]
//   mbextract locres --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] --out DIR   (게임 안 글자 사전 .locres 를 꺼냄)
//   (공통) --kind anim|mesh  --format glb|psk (mesh)  --textures (mesh, 텍스처 포함)
//   mbextract inspect --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] (--paths-file FILE | --filter REGEX [--limit N])
//        파일 안을 실제로 읽어 종류(skeleton/mesh/static/anim/montage)와 연결(스켈레톤 경로, 프레임·트랙 수)을 한 줄씩 알려준다.
//   mbextract players --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] (--paths-file FILE | --filter REGEX [--limit N])
//        '플레이어가 조종하는 캐릭터' 를 데이터로 찾는다: 게임 설정(.ini)의 기본 게임모드, 게임모드의 DefaultPawnClass, 폰(블루프린트)이 쓰는 스켈레탈 메시,
//        카메라/스프링암 컴포넌트, AutoPossessPlayer 를 한 줄씩 알려준다 (type = config | pawn).
//   mbextract export --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] --out DIR --paths-file FILE
// 표준 출력: 한 줄에 JSON 하나 (type = asset | exported | skip | log | done | error)
//
// 주의: CUE4Parse API 는 버전에 따라 시그니처가 바뀐다(특히 DefaultFileProvider 생성자, SubmitKey, OodleHelper).
//       이 파일은 컴파일 검증을 거치지 않았다. 오류가 나면 해당 줄만 맞춰 고치면 된다.
using System.Text.Json;
using System.Text.RegularExpressions;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;

static void Emit(object o) => Console.WriteLine(JsonSerializer.Serialize(o));
static void Fail(string msg) { Emit(new { type = "error", code = "", message = msg }); Environment.Exit(1); }
// 원인을 프로그램이 구분할 수 있게 코드를 붙인 오류 (ERR_OODLE_MISSING / ERR_INVALID_AES / ERR_MISSING_USMAP / ERR_PATH_MISMATCH / ERR_NO_CONTAINERS / ERR_NO_PACKAGES)
static void FailC(string code, string msg) { Emit(new { type = "error", code, message = msg }); Environment.Exit(1); }
static void Warn(string code, string msg) => Emit(new { type = "warn", code, message = msg });

if (args.Length == 0) Fail("사용법: mbextract list|export --game DIR --ue GAME_UE5_4 ...");
var cmd = args[0];
// 지원하는 'UE 버전/게임 프리셋' 전체 목록 (화면의 UE 버전 목록을 이것과 맞춘다). 게임 폴더 없이도 동작.
if (cmd == "list-games" || args.Contains("--list-games"))
{
    Console.WriteLine(JsonSerializer.Serialize(Enum.GetNames<EGame>()));
    return;
}
string? Opt(string name) { for (int i = 1; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1]; return null; }

var game = Opt("--game") ?? "";
var ueName = Opt("--ue") ?? "GAME_UE5_4";
var aes = Opt("--aes");
var usmap = Opt("--usmap");
var kind = (Opt("--kind") ?? "anim").ToLowerInvariant();   // anim | mesh
var meshFmt = (Opt("--format") ?? "glb").ToLowerInvariant(); // glb | psk (mesh 만)
var withTextures = args.Contains("--textures");
if (!Directory.Exists(game)) Fail("게임 폴더가 없습니다: " + game);
if (!Enum.TryParse<EGame>(ueName, true, out var ue))
    Fail("알 수 없는 UE 버전: " + ueName + " (예: GAME_UE5_4)");

try
{
    // Oodle 압축 해제 라이브러리 (UE5 게임 대부분이 사용).
    // ① 게임 폴더 안의 oo2core_*.dll (그 게임이 실제로 쓰는 버전) ② 도우미 옆에 받아 둔 것 ③ 내려받기 순으로 찾는다.
    bool oodleOk = false;
    var walk = new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true };
    try
    {
        string? inGame = null;
        try { inGame = Directory.EnumerateFiles(game, "oo2core_*.dll", walk).OrderByDescending(f => f, StringComparer.OrdinalIgnoreCase).FirstOrDefault(); } catch { }
        if (inGame != null)
        {
            try
            {
                OodleHelper.Initialize(inGame);
                oodleOk = OodleHelper.Instance != null;
                if (oodleOk) Emit(new { type = "log", message = "Oodle: 게임 폴더의 " + Path.GetFileName(inGame) + " 사용" });
            }
            catch (Exception e) { Emit(new { type = "log", message = "게임 폴더의 Oodle 을 쓰지 못함(" + e.Message + ") — 다른 것을 찾습니다" }); }
        }
        if (!oodleOk)
        {
            string? oodle = Path.Combine(AppContext.BaseDirectory, OodleHelper.OodleFileName);
            if (OodleHelper.DownloadOodleDll(ref oodle))
            {
                OodleHelper.Initialize(oodle);
                oodleOk = OodleHelper.Instance != null;
            }
        }
    }
    catch (Exception e) { Emit(new { type = "log", message = "Oodle 준비 중 오류: " + e.Message }); }
    if (!oodleOk) Warn("ERR_OODLE_MISSING", "Oodle 압축 해제 라이브러리를 찾지 못했습니다 (게임 폴더에 oo2core_*.dll 이 없고 내려받기도 실패). 압축된 에셋은 읽지 못할 수 있습니다.");
    try { ZlibHelper.Initialize(Path.Combine(AppContext.BaseDirectory, ZlibHelper.DllName)); }
    catch (Exception e) { Emit(new { type = "log", message = "zlib 준비 실패(일부 오래된 게임만 영향): " + e.Message }); }

    // 컨테이너(.pak/.utoc)가 가장 많이 든 폴더를 읽는다. 이름이 Paks 인 폴더를 우선한다 (--paks 로 직접 지정 가능).
    var root = Opt("--paks") ?? "";
    if (root.Length == 0 || !Directory.Exists(root))
    {
        root = game;
        try
        {
            var best = Directory.EnumerateFiles(game, "*", walk)
                .Where(f => f.EndsWith(".pak", StringComparison.OrdinalIgnoreCase) || f.EndsWith(".utoc", StringComparison.OrdinalIgnoreCase))
                .GroupBy(f => Path.GetDirectoryName(f) ?? game, StringComparer.OrdinalIgnoreCase)
                .OrderByDescending(g => g.Count() + (string.Equals(Path.GetFileName(g.Key), "Paks", StringComparison.OrdinalIgnoreCase) ? 1000 : 0))
                .FirstOrDefault();
            if (best != null) root = best.Key;
        }
        catch { }
    }
    Emit(new { type = "log", message = "읽는 폴더: " + root });
    var provider = new DefaultFileProvider(root, SearchOption.TopDirectoryOnly, new VersionContainer(ue), StringComparer.OrdinalIgnoreCase);
    bool usmapGiven = !string.IsNullOrEmpty(usmap);
    if (usmapGiven)
    {
        try { provider.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap!); }
        catch (Exception e) { FailC("ERR_PATH_MISMATCH", "usmap(매핑) 파일을 읽지 못했습니다: " + e.Message + " — 파일이 깨졌거나 이 게임/버전의 것이 아닐 수 있습니다."); }
    }
    provider.Initialize();
    var allVfs = provider.UnloadedVfs.ToList();
    int nAll = allVfs.Count, nEnc = allVfs.Count(r => r.IsEncrypted);
    bool hasIo = false;
    try { hasIo = Directory.EnumerateFiles(root, "*.utoc", SearchOption.TopDirectoryOnly).Any(); } catch { }
    Emit(new { type = "log", message = "컨테이너 " + nAll + "개 발견 (암호화 " + nEnc + "개" + (hasIo ? ", IoStore" : "") + ")" });
    if (nAll == 0) FailC("ERR_NO_CONTAINERS", "이 폴더에서 .pak / .utoc 컨테이너를 찾지 못했습니다: " + root);
    // 암호화 안 된 컨테이너는 Mount() 로, 키가 있으면 SubmitKey 로 연다.
    int mounted = provider.Mount();
    bool keyGiven = !string.IsNullOrEmpty(aes);
    string keyErr = "";
    if (keyGiven)
    {
        try { mounted += provider.SubmitKey(new FGuid(), new FAesKey(aes!)); }
        catch (Exception e) { keyErr = e.GetType().Name + ": " + e.Message; }
    }
    provider.PostMount();
    var locked = provider.UnloadedVfs.Where(r => r.IsEncrypted).ToList();
    var lockedNames = locked.Select(r => r.Name).Take(8).ToList();
    var lockedGuids = locked.Select(r => r.EncryptionKeyGuid.ToString()).Distinct().Take(8).ToList();
    int pkgCount = provider.Files.Keys.Count(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase));
    Emit(new { type = "containers", total = nAll, encrypted = nEnc, mounted, locked = lockedNames, key_guids = lockedGuids, iostore = hasIo,
               files = provider.Files.Count, packages = pkgCount, oodle = oodleOk, usmap = usmapGiven, ue = ue.ToString() });
    Emit(new { type = "log", message = "마운트 " + mounted + "개, 전체 파일 " + provider.Files.Count + "개, 패키지(.uasset) " + pkgCount + "개" });
    string aesMsg = keyGiven
        ? "AES 키가 이 게임과 맞지 않습니다 (암호화된 컨테이너 " + locked.Count + "개를 열지 못함" + (keyErr.Length > 0 ? ", " + keyErr : "") + "). 키를 다시 확인하거나, 커스텀 엔진 게임이면 UE 버전을 그 게임 전용 항목으로 바꿔 보세요."
        : "암호화된 게임인데 AES 키가 없습니다 (암호화된 컨테이너 " + locked.Count + "개).";
    if (pkgCount == 0)
    {
        if (locked.Count > 0) FailC("ERR_INVALID_AES", aesMsg);
        FailC("ERR_NO_PACKAGES", "컨테이너는 열었지만 에셋(.uasset)이 하나도 없습니다 (마운트 " + mounted + "개). UE 버전이 맞지 않을 수 있습니다 — 게임 전용 항목이 있으면 그것을 고르세요.");
    }
    if (locked.Count > 0) Warn("ERR_INVALID_AES", aesMsg + " 열린 컨테이너의 에셋만 보입니다.");
    if (hasIo && !usmapGiven && ue >= EGame.GAME_UE5_0)
        Warn("ERR_MISSING_USMAP", "언리얼 5 IoStore 게임인데 매핑(.usmap) 파일이 없습니다. 목록은 보이지만 에셋 내용을 읽을 때 실패할 수 있습니다.");

    if (cmd == "probe")
    {
        // 실제로 에셋 몇 개를 열어 보고 '왜 안 되는지'를 코드로 알려 준다 (AES 키 후보 확인, usmap 확인에 쓴다).
        int want = int.TryParse(Opt("--limit"), out var pl) ? pl : 6;
        var samples = provider.Files.Keys.Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
            .OrderBy(k => Regex.IsMatch(k, "(?i)skeleton|/sk_|anim") ? 0 : 1).ThenBy(k => k, StringComparer.OrdinalIgnoreCase).Take(want).ToList();
        int okN = 0, mapErr = 0, oodleErr = 0, otherErr = 0; string firstErr = "";
        foreach (var sp in samples)
        {
            try
            {
                var pkg = provider.LoadPackage(sp);
                int nExp = pkg.GetExports().Count();
                okN++;
            }
            catch (Exception e)
            {
                var txt = e.ToString();
                if (Regex.IsMatch(txt, "(?i)mapping|unversioned|usmap")) mapErr++;
                else if (Regex.IsMatch(txt, "(?i)oodle")) oodleErr++;
                else otherErr++;
                if (firstErr.Length == 0) firstErr = e.GetType().Name + ": " + e.Message;
            }
        }
        string code = "OK", msg = "에셋 " + okN + "/" + samples.Count + "개를 정상으로 읽었습니다.";
        if (okN == 0 && samples.Count > 0)
        {
            if (mapErr > 0 && !usmapGiven) { code = "ERR_MISSING_USMAP"; msg = "매핑(.usmap) 파일이 없어 에셋 내용을 읽지 못합니다."; }
            else if (mapErr > 0) { code = "ERR_PATH_MISMATCH"; msg = "매핑(.usmap) 파일이 이 게임(또는 이 버전)과 맞지 않습니다. 게임이 패치됐다면 새 매핑 파일이 필요합니다."; }
            else if (oodleErr > 0 || !oodleOk) { code = "ERR_OODLE_MISSING"; msg = "Oodle 압축을 풀지 못했습니다."; }
            else { code = "ERR_READ_FAILED"; msg = "에셋을 읽지 못했습니다. UE 버전(게임 전용 항목)이 맞는지 확인하세요."; }
        }
        Emit(new { type = "probe", code, message = msg, ok = okN, tried = samples.Count, map_errors = mapErr, oodle_errors = oodleErr, other_errors = otherErr, first_error = firstErr });
        Emit(new { type = "done", count = okN });
    }
    else if (cmd == "list")
    {
        var rx = new Regex(Opt("--filter") ?? "(?i)anim", RegexOptions.Compiled);
        int limit = int.TryParse(Opt("--limit"), out var l) ? l : 5000, n = 0;
        foreach (var key in provider.Files.Keys.OrderBy(k => k, StringComparer.OrdinalIgnoreCase))
        {
            if (!key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || !rx.IsMatch(key)) continue;
            Emit(new { type = "asset", path = key });
            if (++n >= limit) break;
        }
        Emit(new { type = "done", count = n });
    }
    else if (cmd == "inspect")
    {
        // 파일 이름이 아니라 파일 안의 데이터로 종류와 연결 관계를 알아낸다. CUE4Parse 버전마다 속성 이름/타입이 달라서
        // 값 읽기는 리플렉션으로 하고, 못 읽으면 빈 값으로 두어(파이썬 쪽이 '확인 안 됨'으로 처리) 컴파일/실행이 깨지지 않게 한다.
        var listFile = Opt("--paths-file");
        List<string> targets;
        if (!string.IsNullOrEmpty(listFile)) targets = File.ReadAllLines(listFile).Where(x => x.Trim().Length > 0).ToList();
        else
        {
            var frx = new Regex(Opt("--filter") ?? "(?i)anim", RegexOptions.Compiled);
            int lim = int.TryParse(Opt("--limit"), out var l2) ? l2 : 20000;
            targets = provider.Files.Keys.Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) && frx.IsMatch(k))
                .OrderBy(k => k, StringComparer.OrdinalIgnoreCase).Take(lim).ToList();
        }
        int done = 0, found = 0;
        foreach (var p in targets)
        {
            done++;
            try
            {
                if (!provider.TryLoadPackage(p, out var pkg) || pkg is null) { Emit(new { type = "node", path = p, kind = "unreadable" }); continue; }
                foreach (var obj in pkg.GetExports())
                {
                    if (obj is UAnimSequence seq)
                    {
                        var skPath = PathOf(Get(obj, "Skeleton"));
                        Emit(new { type = "node", path = p, kind = "anim", name = obj.Name, skeleton = skPath, reason = skPath.Length > 0 ? "" : SkelDiag(obj),
                                   frames = IntOf(Get(obj, "NumFrames")), length = FloatOf(Get(obj, "SequenceLength")), rate = FloatOf(Get(obj, "RateScale")),
                                   tracks = TracksOf(obj),
                                   compressed = Get(obj, "CompressedDataStructure") != null || Get(obj, "CompressedData") != null,
                                   additive = Convert.ToString(Get(obj, "AdditiveAnimType")) ?? "" });
                        found++;
                    }
                    else if (obj is UAnimMontage || obj is UAnimComposite)
                    {
                        var refs = new List<string>();
                        try { foreach (var sq in SequencesOf(obj)) refs.Add(PathOf(sq)); } catch { }
                        Emit(new { type = "node", path = p, kind = "montage", name = obj.Name, skeleton = PathOf(Get(obj, "Skeleton")), sequences = refs });
                        found++;
                    }
                    else if (obj is USkeletalMesh)
                    {
                        var skPathM = PathOf(Get(obj, "Skeleton"));
                        Emit(new { type = "node", path = p, kind = "mesh", name = obj.Name, skeleton = skPathM, reason = skPathM.Length > 0 ? "" : SkelDiag(obj),
                                   bones = CountOf(Get(Get(obj, "ReferenceSkeleton"), "FinalRefBoneInfo")) });
                        found++;
                    }
                    else if (obj is UStaticMesh)
                    {
                        Emit(new { type = "node", path = p, kind = "static", name = obj.Name });
                        found++;
                    }
                    else if (obj is USkeleton)
                    {
                        Emit(new { type = "node", path = p, kind = "skeleton", name = obj.Name,
                                   bones = CountOf(Get(Get(obj, "ReferenceSkeleton"), "FinalRefBoneInfo")) });
                        found++;
                    }
                }
            }
            catch (Exception e) { Emit(new { type = "node", path = p, kind = "unreadable", reason = e.GetType().Name + ": " + e.Message }); }
            if (done % 25 == 0) Emit(new { type = "progress", done, total = targets.Count });
        }
        Emit(new { type = "done", count = found });
    }
    else if (cmd == "players")
    {
        // 1) 게임 설정(.ini): 기본 게임모드/폰 클래스. 파일이 없거나 지워져 있을 수 있다.
        try
        {
            foreach (var key in provider.Files.Keys.Where(k => Regex.IsMatch(k, @"(?i)(^|/)(Default)?(Engine|Game)\.ini$")))
            {
                try
                {
                    if (!provider.TrySaveAsset(key, out var data) || data is null || data.Length == 0) continue;
                    foreach (var line in System.Text.Encoding.UTF8.GetString(data).Split('\n'))
                    {
                        var ln = line.Trim();
                        var m = Regex.Match(ln, @"(?i)^[+.]?(GlobalDefaultGameMode|GameDefaultMap|DefaultPawnClass|DefaultPlayerControllerClass|GameInstanceClass)\s*=\s*(.+)$");
                        if (m.Success) Emit(new { type = "config", file = key, key = m.Groups[1].Value, value = m.Groups[2].Value.Trim().Trim('"') });
                    }
                }
                catch { }
            }
        }
        catch { }
        // 2) 블루프린트: 대상 목록은 파일 이름 규칙으로 좁히되(전부 열면 너무 오래 걸림), 플레이어인지는 안에 든 데이터로 판단한다.
        var listFile = Opt("--paths-file");
        List<string> targets;
        if (!string.IsNullOrEmpty(listFile)) targets = File.ReadAllLines(listFile).Where(x => x.Trim().Length > 0).ToList();
        else
        {
            var frx = new Regex(Opt("--filter") ?? @"(?i)/(bp|b|bpc|gm|pc|abp)_[^/]*\.uasset$|gamemode|pawn|player|character|hero|protagonist", RegexOptions.Compiled);
            int lim = int.TryParse(Opt("--limit"), out var l3) ? l3 : 30000;
            targets = provider.Files.Keys.Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) && frx.IsMatch(k))
                .OrderBy(k => k, StringComparer.OrdinalIgnoreCase).Take(lim).ToList();
        }
        int done = 0, found = 0;
        foreach (var p in targets)
        {
            done++;
            try
            {
                if (!provider.TryLoadPackage(p, out var pkg) || pkg is null) continue;
                var meshes = new List<string>(); var animClasses = new List<string>();
                bool camera = false; string parent = "", autoPossess = "", defPawn = "", ctrl = "", className = "";
                foreach (var obj in pkg.GetExports())
                {
                    var et = Convert.ToString(Get2(obj, "ExportType")) ?? obj.GetType().Name;
                    var tn = obj.GetType().Name;
                    if (et.Contains("SkeletalMeshComponent") || tn.Contains("SkeletalMeshComponent"))
                    {
                        var mp = PathOf(Get(obj, "SkeletalMeshAsset"));
                        if (mp.Length == 0) mp = PathOf(Get(obj, "SkeletalMesh"));
                        if (mp.Length > 0 && !meshes.Contains(mp)) meshes.Add(mp);
                        var ac = PathOf(Get(obj, "AnimClass"));
                        if (ac.Length > 0 && !animClasses.Contains(ac)) animClasses.Add(ac);
                    }
                    else if (et.Contains("CameraComponent") || et.Contains("SpringArmComponent") || tn.Contains("CameraComponent") || tn.Contains("SpringArmComponent")) camera = true;
                    else if (et.Contains("BlueprintGeneratedClass") || tn.Contains("BlueprintGeneratedClass"))
                    {
                        parent = PathOf(Get(obj, "SuperStruct")); className = obj.Name;
                    }
                    if (obj.Name.StartsWith("Default__", StringComparison.Ordinal))
                    {
                        var ap = Convert.ToString(Unwrap(Get(obj, "AutoPossessPlayer"))) ?? "";
                        if (ap.Length > 0) autoPossess = ap;
                        var dp = PathOf(Get(obj, "DefaultPawnClass"));
                        if (dp.Length > 0) defPawn = dp;
                        var pc = PathOf(Get(obj, "PlayerControllerClass"));
                        if (pc.Length > 0) ctrl = pc;
                    }
                }
                if (meshes.Count == 0 && !camera && defPawn.Length == 0 && autoPossess.Length == 0) continue;
                Emit(new { type = "pawn", path = p, name = className, parent, meshes, anim_classes = animClasses, camera, auto_possess = autoPossess,
                           default_pawn = defPawn, controller = ctrl });
                found++;
            }
            catch (Exception e) { Emit(new { type = "log", message = "플레이어 확인 중 건너뜀 " + p + ": " + e.GetType().Name }); }
            if (done % 25 == 0) Emit(new { type = "progress", done, total = targets.Count });
        }
        Emit(new { type = "done", count = found });
    }
    else if (cmd == "locres")
    {
        // 게임 안에 표시되는 글자(몬스터 이름 등)가 들어 있는 .locres 원본 파일을 그대로 저장한다. 해석은 파이썬 쪽에서 한다.
        var outDir = Opt("--out") ?? Fail2("--out 이 필요합니다");
        Directory.CreateDirectory(outDir);
        int n = 0;
        foreach (var key in provider.Files.Keys.Where(k => k.EndsWith(".locres", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k, StringComparer.OrdinalIgnoreCase))
        {
            try
            {
                if (!provider.TrySaveAsset(key, out var data) || data is null || data.Length == 0) continue;
                var dest = Path.Combine(outDir, key.Replace('/', '_').Replace('\\', '_'));
                File.WriteAllBytes(dest, data);
                Emit(new { type = "exported", path = key, file = dest });
                n++;
            }
            catch (Exception e) { Emit(new { type = "skip", path = key, reason = e.GetType().Name + ": " + e.Message }); }
        }
        if (n == 0) Emit(new { type = "log", message = "이 게임에서 .locres(글자 사전)를 찾지 못했습니다" });
        Emit(new { type = "done", count = n });
    }
    else if (cmd == "export")
    {
        var outDir = Opt("--out") ?? Fail2("--out 이 필요합니다");
        var listFile = Opt("--paths-file") ?? Fail2("--paths-file 이 필요합니다");
        var paths = File.ReadAllLines(listFile).Where(s => s.Trim().Length > 0).ToList();
        var opts = new ExportOptions(
            meshFormat: kind == "mesh" && meshFmt == "glb" ? EMeshFormat.Gltf2 : EMeshFormat.ActorX,
            exportMaterials: kind == "mesh" && withTextures);
        int ok = 0;
        foreach (var p in paths)
        {
            try
            {
                if (!provider.TryLoadPackage(p, out var pkg) || pkg is null)
                {
                    Emit(new { type = "skip", path = p, reason = "패키지를 열지 못함" });
                    continue;
                }
                var session = new ExportSession();
                int queued = 0;
                var queuedObjs = new HashSet<UObject>();
                bool montage = false;
                foreach (var obj in pkg.GetExports())
                {
                    if (kind == "mesh")
                    {
                        if ((obj is USkeletalMesh || obj is UStaticMesh) && queuedObjs.Add(obj)) { session.Add(obj); queued++; }  // 스태틱(리깅 없는) 메시도
                    }
                    else if (obj is UAnimSequence)
                    {
                        if (queuedObjs.Add(obj)) { session.Add(obj); queued++; }
                    }
                    else if (obj is UAnimMontage || obj is UAnimComposite)
                    {
                        // 몽타주/컴포지트는 애니메이션 자체가 아니라 AnimSequence 를 이어 붙인 것이라, 참조된 AnimSequence 를 꺼낸다
                        foreach (var seq in SequencesOf(obj))
                            if (queuedObjs.Add(seq)) { session.Add(seq); queued++; montage = true; }
                    }
                }
                if (montage) Emit(new { type = "log", message = "몽타주에서 AnimSequence " + queued + "개를 찾음: " + p });
                if (queued == 0)
                {
                    Emit(new { type = "skip", path = p, reason = kind == "mesh" ? "스켈레탈/스태틱 메시가 아님" : "AnimSequence 도, 그것을 참조하는 몽타주도 아님" });
                    continue;
                }
                var results = session.RunAsync(outDir, opts).GetAwaiter().GetResult();
                bool any = false;
                foreach (var r in results)
                {
                    if (r.Success && r.DiskFilePaths is { Count: > 0 })
                    {
                        foreach (var f in r.DiskFilePaths) Emit(new { type = "exported", path = p, file = f });
                        ok++; any = true;
                    }
                    else Emit(new { type = "skip", path = p, reason = r.Error?.Message ?? "내보내지 못함" });
                }
                if (!any) Emit(new { type = "log", message = "내보낸 파일 없음: " + p });
            }
            catch (Exception e) { Emit(new { type = "skip", path = p, reason = e.GetType().Name + ": " + e.Message }); }
        }
        Emit(new { type = "done", count = ok });
    }
    else Fail("알 수 없는 명령: " + cmd);
}
catch (Exception e) { Fail(e.GetType().Name + ": " + e.Message); }

static IEnumerable<UAnimSequence> SequencesOf(UObject obj)
{
    var tracks = new List<FAnimTrack>();
    if (obj is UAnimMontage m && m.SlotAnimTracks != null)
        foreach (var st in m.SlotAnimTracks)
            if (st?.AnimTrack != null) tracks.Add(st.AnimTrack);
    if (obj is UAnimComposite c && c.AnimationTrack != null) tracks.Add(c.AnimationTrack);
    foreach (var t in tracks)
    {
        if (t.AnimSegments == null) continue;
        foreach (var seg in t.AnimSegments)
        {
            if (seg?.AnimReference != null && seg.AnimReference.TryLoad(out UAnimSequence seq) && seq != null) yield return seq;
        }
    }
}

// ---- 리플렉션 도우미: API 이름이 버전마다 달라도 컴파일은 되게 한다
static object? Get(object? o, string name)
{
    if (o == null) return null;
    const System.Reflection.BindingFlags f = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
    try
    {
        var t = o.GetType();
        var p = t.GetProperty(name, f);
        if (p != null && p.GetIndexParameters().Length == 0) { var v = p.GetValue(o); if (v != null) return v; }
        var fi = t.GetField(name, f);
        if (fi != null) { var v = fi.GetValue(o); if (v != null) return v; }
    }
    catch { }
    // 멤버로 없으면 파일에 저장된 속성 목록(Properties)에서 같은 이름을 찾는다 (버전마다 멤버로 안 풀어 두는 값이 있다)
    try
    {
        if (o is UObject)
        {
            var props = o.GetType().GetProperty("Properties", f)?.GetValue(o) as System.Collections.IEnumerable;
            if (props != null)
                foreach (var tag in props)
                {
                    var nm = Get2(tag, "Name")?.ToString();
                    if (nm == name) return Get2(tag, "Tag");
                }
        }
    }
    catch { }
    return null;
}

// Properties 목록 안에서만 쓰는 단순 읽기 (Get 이 서로를 부르며 무한히 돌지 않게)
static object? Get2(object? o, string name)
{
    if (o == null) return null;
    const System.Reflection.BindingFlags f = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
    try
    {
        var t = o.GetType();
        var p = t.GetProperty(name, f);
        if (p != null && p.GetIndexParameters().Length == 0) return p.GetValue(o);
        var fi = t.GetField(name, f);
        if (fi != null) return fi.GetValue(o);
    }
    catch { }
    return null;
}

// 값 상자(FPropertyTagType 등)를 벗겨서 안의 값을 꺼낸다
static object? Unwrap(object? o)
{
    for (int i = 0; i < 4 && o != null; i++)
    {
        if (o is string || o.GetType().IsPrimitive || o is Enum || o is System.Collections.ICollection) return o;
        var g = Get2(o, "GenericValue") ?? Get2(o, "Value");
        if (g == null || ReferenceEquals(g, o)) return o;
        o = g;
    }
    return o;
}

static string PathOf(object? o, int depth = 0)
{
    if (o == null || depth > 5) return "";
    try
    {
        if (o is string s) return s;
        var t = o.GetType();
        // GetPathName 은 선택 인자가 있는 경우가 많아 빈 인자 목록으로는 못 찾는다 -> 이름으로 찾아 기본값으로 부른다
        foreach (var m in t.GetMethods(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance))
        {
            if (m.Name != "GetPathName") continue;
            var ps = m.GetParameters();
            if (ps.Any(x => !x.IsOptional)) continue;
            var r = m.Invoke(o, ps.Select(x => Type.Missing).ToArray()) as string;
            if (!string.IsNullOrEmpty(r)) return r!;
        }
        // Lazy<T> / FPackageIndex / 속성 값 상자: 안쪽으로 한 겹씩 벗겨서 찾는다
        foreach (var n in new[] { "ResolvedObject", "Value", "Object", "GenericValue", "Tag" })
        {
            var inner = Get2(o, n);
            if (inner != null && !ReferenceEquals(inner, o)) { var r = PathOf(inner, depth + 1); if (r.Length > 0) return r; }
        }
        // 오브젝트: 패키지 이름 + 오브젝트 이름
        var owner = Get2(o, "Owner");
        var nm = Get2(o, "Name")?.ToString();
        if (owner != null)
        {
            var pk = Get2(owner, "Name")?.ToString();
            if (!string.IsNullOrEmpty(pk)) return pk + (string.IsNullOrEmpty(nm) ? "" : "." + nm);
            var op = PathOf(owner, depth + 1);
            if (op.Length > 0) return op;
        }
        var pkgName = Get2(o, "Package") is object pkgObj ? Get2(pkgObj, "Name")?.ToString() : null;
        if (!string.IsNullOrEmpty(pkgName)) return pkgName + (string.IsNullOrEmpty(nm) ? "" : "." + nm);
    }
    catch { }
    return "";
}

// 스켈레톤 경로를 못 읽었을 때만 이유(어떤 타입이 들어 있었는지)를 남긴다 — 파이썬이 상태줄에 보여 줘서 원인을 알 수 있게
static string SkelDiag(UObject obj)
{
    try
    {
        var sk = Get(obj, "Skeleton");
        if (sk != null && PathOf(sk).Length > 0) return "";
        return "skeleton=" + (sk == null ? "null" : sk.GetType().Name);
    }
    catch (Exception e) { return "skeleton 읽기 예외 " + e.GetType().Name; }
}

// 키(트랙) 수. 어느 쪽 목록도 못 읽었으면 -1 (없다는 뜻이 아니라 '모름')
static int TracksOf(UObject obj)
{
    int n = 0; bool any = false;
    foreach (var nm in new[] { "TrackToSkeletonMapTable", "CompressedTrackToSkeletonMapTable", "RawAnimationData", "AnimationTrackNames" })
    {
        var v = Get(obj, nm);
        if (v != null) { any = true; n += CountOf(v); }
    }
    return any ? n : -1;
}

static int IntOf(object? o) { try { o = Unwrap(o); return o == null ? -1 : Convert.ToInt32(o); } catch { return -1; } }
static double FloatOf(object? o) { try { o = Unwrap(o); return o == null ? -1 : Convert.ToDouble(o); } catch { return -1; } }
static int CountOf(object? o)
{
    try
    {
        if (o == null) return 0;
        if (o is System.Collections.ICollection c) return c.Count;
        if (o is Array a) return a.Length;
        var len = Get(o, "Length") ?? Get(o, "Count");
        return len == null ? 0 : Convert.ToInt32(len);
    }
    catch { return 0; }
}

static string Fail2(string msg) { Fail(msg); return ""; }
