// MotionBridge 추출 도우미 (CUE4Parse 기반).
//   mbextract list   --game DIR --ue GAME_UE5_4 [--aes 0x...] [--usmap FILE] [--filter REGEX] [--limit N]
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
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Objects.Core.Misc;
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

    var provider = new DefaultFileProvider(game, SearchOption.AllDirectories, new VersionContainer(ue), StringComparer.OrdinalIgnoreCase);
    provider.Initialize();
    if (!string.IsNullOrEmpty(aes)) provider.SubmitKey(new FGuid(), new FAesKey(aes));
    if (!string.IsNullOrEmpty(usmap)) provider.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
    provider.PostMount();
    Emit(new { type = "log", message = "마운트된 파일 " + provider.Files.Count + "개" });
    if (provider.Files.Count == 0) Fail("읽을 수 있는 파일이 없습니다. AES 키가 필요하거나 UE 버전이 맞지 않을 수 있습니다.");

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
    else if (cmd == "export")
    {
        var outDir = Opt("--out") ?? Fail2("--out 이 필요합니다");
        var listFile = Opt("--paths-file") ?? Fail2("--paths-file 이 필요합니다");
        var paths = File.ReadAllLines(listFile).Where(s => s.Trim().Length > 0).ToList();
        var opts = new ExportOptions(meshFormat: EMeshFormat.ActorX, exportMaterials: false);
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
                foreach (var obj in pkg.GetExports())
                {
                    if (obj is not UAnimSequence anim) continue;
                    session.Add(anim);
                    queued++;
                }
                if (queued == 0)
                {
                    Emit(new { type = "skip", path = p, reason = "AnimSequence 가 아님" });
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

static string Fail2(string msg) { Fail(msg); return ""; }
