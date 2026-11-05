// MotionBridge 추출 도우미 (CUE4Parse 기반).
//   mbextract list   --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] [--filter REGEX] [--limit N]
//   mbextract locres --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] --out DIR   (게임 안 글자 사전 .locres 를 꺼냄)
//   (공통) --kind anim|mesh  --format glb|psk (mesh)  --textures (mesh, 텍스처 포함)
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
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;

static void Emit(object o) => Console.WriteLine(JsonSerializer.Serialize(o));
static void Fail(string msg) { Emit(new { type = "error", message = msg }); Environment.Exit(1); }

if (args.Length == 0) Fail("사용법: mbextract list|export --game DIR --ue GAME_UE5_4 ...");
var cmd = args[0];
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
    // Oodle 압축 해제 라이브러리 (UE5 게임 대부분이 사용). 없으면 내려받는다.
    try
    {
        string? oodle = Path.Combine(AppContext.BaseDirectory, OodleHelper.OodleFileName);
        if (!OodleHelper.DownloadOodleDll(ref oodle)) throw new Exception("oodle 라이브러리를 내려받지 못했습니다");
        OodleHelper.Initialize(oodle);
    }
    catch (Exception e) { Emit(new { type = "log", message = "Oodle 준비 실패(압축된 에셋을 못 읽을 수 있음): " + e.Message }); }

    // Paks 폴더가 있으면 그 폴더만 읽는다(게임 루트 전체를 뒤지지 않음).
    var root = game;
    try { root = Directory.EnumerateDirectories(game, "Paks", SearchOption.AllDirectories).FirstOrDefault() ?? game; } catch { }
    Emit(new { type = "log", message = "읽는 폴더: " + root });
    var provider = new DefaultFileProvider(root, SearchOption.TopDirectoryOnly, new VersionContainer(ue), StringComparer.OrdinalIgnoreCase);
    if (!string.IsNullOrEmpty(usmap)) provider.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
    provider.Initialize();
    Emit(new { type = "log", message = "컨테이너 " + provider.UnloadedVfs.Count + "개 발견" });
    // 암호화 안 된 컨테이너는 Mount() 로, 키가 있으면 SubmitKey 로 연다.
    int mounted = provider.Mount();
    if (!string.IsNullOrEmpty(aes)) mounted += provider.SubmitKey(new FGuid(), new FAesKey(aes));
    provider.PostMount();
    int pkgCount = provider.Files.Keys.Count(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase));
    Emit(new { type = "log", message = "마운트 " + mounted + "개, 전체 파일 " + provider.Files.Count + "개, 패키지(.uasset) " + pkgCount + "개" });
    if (pkgCount == 0) Fail("읽을 수 있는 에셋이 없습니다(마운트 " + mounted + "개). AES 키가 필요하거나 UE 버전/usmap 이 맞지 않을 수 있습니다.");

    if (cmd == "list")
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
                        if (obj is USkeletalMesh && queuedObjs.Add(obj)) { session.Add(obj); queued++; }
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
                    Emit(new { type = "skip", path = p, reason = kind == "mesh" ? "스켈레탈 메시가 아님" : "AnimSequence 도, 그것을 참조하는 몽타주도 아님" });
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

static string Fail2(string msg) { Fail(msg); return ""; }
