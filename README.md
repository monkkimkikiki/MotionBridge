# MotionBridge

게임에서 뽑은 모션을 **내 모델**에 자동 리타깃하고, **Max / Maya / Blender** 용으로 내보내는 간이 에디터.

- 소스 모션(FBX / GLB / BVH / .blend) + 내 모델(스켈레톤 있는 FBX / GLB / .blend) → 자동 본 매핑 → 리타깃
- 리타깃을 **실시간으로 조절**: 스케일, 골반(COM) 위치, 루트 회전, 제자리 모드, 발 IK(접지/바닥 높이/골반 낮추기), 본별 회전 보정
- 품질 검수: 발 미끄러짐, 바닥 뚫림, 다리 안 닿음, 회전 튐 구간을 찾아서 클릭하면 그 프레임으로 이동
- 내보내기: 대상 프로그램(Max/Maya/Blender) 체크박스, 형식(BVH / GLB / FBX), **본 구조 포함 여부 체크박스**, 현재 클립 / 모든 클립
- 인간형은 자동 매핑, 크리처/구조물은 "원본 모션 그대로 + 본 구조 포함"으로 본 세팅째 내보내기

**게임에서 바로 추출**도 지원합니다(언리얼 엔진 게임): 게임 폴더를 고르면 암호화 여부를 검사하고, 암호화돼 있을 때만 AES 키 입력을 요구하고, 아니면 바로 애니메이션을 검색·추출합니다.
FBX/GLB/BVH/PSA/.blend 로 이미 뽑아 둔 파일을 직접 열 수도 있습니다.

> 본인이 구매한 게임의 에셋을 개인 학습·제작 참고용으로 쓰세요. 원본 에셋의 재배포/상업적 사용은 저작권·약관 문제가 될 수 있습니다.

## 실행

### 소스에서
```
pip install -r requirements.txt        # numpy, PySide6
python MotionBridge.py                 # GUI
python -m motionbridge info 파일.fbx    # CLI
```

### exe / 설치 프로그램 만들기 (Windows)
```
packaging\build_windows.bat
```
테스트 → PyInstaller → (Inno Setup 이 있으면) 설치 프로그램까지 만듭니다.
설치 프로그램은 **설치 위치를 고르는 페이지**가 있고, 관리자 권한 없이 사용자 폴더에도 설치할 수 있습니다.
GitHub 에 올리면 `.github/workflows/build-windows.yml` 이 같은 일을 자동으로 합니다.
설치 마법사 문구는 영어입니다(Inno Setup 기본). 한국어로 바꾸려면 `Korean.isl` 을 구해 `installer.iss` 의 `[Languages]` 에 추가하세요.

## 게임에서 추출 (탭 "0. 게임에서 추출")

1. **게임 폴더** 지정 → **검사**: `Content/Paks` 의 `.pak` / `.utoc` 를 읽어 암호화 여부, 형식, 크기를 보여 줍니다(수십 GB 파일은 열지 않고 헤더만 읽음).
2. **암호화 안 된 게임**: AES 키 칸이 비활성화되고 바로 진행. **암호화된 게임**: 키가 없으면 진행을 막고, 있으면 형식(0x+64자리 16진수 / Base64)을 검증해서 사용. AES 키는 프로그램이 구해 주지 않습니다 — 게임/빌드마다 달라서 모딩 커뮤니티 자료 등에서 직접 찾아 넣어야 합니다.
3. **UE 버전**, **mappings(.usmap)** 지정(언리얼 5 는 보통 필요). usmap 은 게임을 실행해서 UE4SS / Dumper-7 로 만드는 파일이라 프로그램이 대신 만들 수 없습니다.
4. **종류**를 고르고(애니메이션 PSA / 메시 GLB / 메시 PSK) 검색식(정규식)으로 에셋을 찾아 → 고른 것을 추출 → 추출된 파일을 열기.
   애니메이션은 **소스 모션으로**, 메시(GLB)는 **타깃 모델로** 열 수 있습니다(스켈레톤+스킨 포함, 체크하면 머티리얼/텍스처도). 메시 기본 검색식은 `/SK…_` 로 시작하는 이름입니다(게임마다 이름 규칙이 달라서 직접 바꿔야 할 수 있음).
   (PSA 한 파일에 스켈레톤과 모션이 함께 있어서 Blender 를 거치지 않고 바로 리타깃됩니다.)

CLI: `python -m motionbridge extract scan --game "D:\...\Sparta"` / `extract list ...` / `extract export ... --out 폴더` (메시는 `--kind mesh --format glb|psk [--textures]`)

실제 추출은 **추출 도우미 `mbextract`** (CUE4Parse = FModel 이 쓰는 라이브러리 기반, `tools/mbextract`)가 합니다.
빌드: .NET 8 SDK + git 이 있는 Windows 에서 `packaging\build_windows.bat`(자동) 또는
```
git clone --depth 1 --recursive https://github.com/FabianFG/CUE4Parse third_party/CUE4Parse
dotnet publish tools/mbextract -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o tools/mbextract/out
```
도우미가 없어도 나머지 기능은 모두 동작합니다.

> Mortal Shell 2 (Steam 폴더명 `Sparta`) 는 pak/IoStore 가 **암호화돼 있지 않음**을 실제 파일 헤더로 확인했습니다(AES 키 불필요). 다만 UE5 라 mappings(.usmap) 는 필요합니다.

## 3ds Max Biped 로 바로 내보내기

FBX 는 Biped 에 직접 들어가지 않고(.bip 는 비공개 바이너리라 이 프로그램이 직접 쓸 수 없음) FBX → Biped 변환에 손이 한 번 더 갑니다. 내보내기 탭에서 **"3ds Max Biped (.ms)"** 를 체크하면 다음 두 파일이 만들어집니다.

- `<이름>_biped.ms` — 3ds Max 에서 **Scripting > Run Script** 로 실행하는 MaxScript
- `<이름>_biped.mbbip` — 프레임별 회전 데이터 (스크립트와 **같은 폴더**에 두기)

스크립트가 하는 일: Biped 를 모델 키에 맞춰 새로 만들고 → Figure 모드에서 내 모델 바인드 포즈의 팔다리 방향에 맞추고 → 프레임마다 키를 넣고 → 저장 위치를 물어 `.bip` 로 저장. 그 `.bip` 를 Biped 의 Load File 로 다른 Biped 에도 쓸 수 있습니다. 회전은 바인드 포즈 대비 월드 변화량으로 보내서 본 축 방향이 달라도 맞습니다. 리타깃 결과/소스 모션 모두 가능하고, 매핑된 부위만 들어갑니다(Hips 필수). CLI: `--formats bip`.

**미검증**: 3ds Max 가 없는 환경에서 만들어서 MaxScript 는 실행해 보지 못했습니다(데이터 계산과 파일 형식만 시험). Biped 함수 이름·인자는 문서 기준이고, 안 맞는 곳은 스크립트가 실패한 부위를 메시지로 알려 줍니다. Biped 의 팔다리 길이는 내 모델과 달라서 발 미끄러짐이 생길 수 있습니다.

## 3D 뷰: 메시 보기 방식과 단축키

오른쪽 패널의 **메시** 콤보(또는 숫자키)로 고릅니다: `0` 표시 안 함 · `1` 점 · `2` 와이어 · `3` 반투명 · `4` 솔리드 · `5` 머티리얼(GLB 에 머티리얼/텍스처가 있을 때 색·텍스처 표시). `M` 은 순서대로 순환합니다. 뼈대·겹쳐 보기·IK·카메라 따라가기는 체크박스(또는 `B` `G` `T`)로 켜고 끕니다.

| 동작 | 단축키 |
|---|---|
| 소스 모션 / 타깃 모델 열기 | Ctrl+O / Ctrl+Shift+O |
| 프로필 불러오기 / 저장 | Ctrl+Shift+P / Ctrl+S |
| 내보내기 · 종료 | Ctrl+E · Ctrl+Q |
| **실행 취소 / 다시 실행** | **Ctrl+Z** / Ctrl+Y 또는 Ctrl+Shift+Z |
| 자동 매핑 다시 하기 · 설정 기본값 | Ctrl+M · Ctrl+Shift+R |
| 재생/정지 | Space |
| 프레임 이동 | `,` `.` (1프레임) · Ctrl+←/→ (10프레임) · Ctrl+Home/End (처음/끝) |
| 화면 맞추기 | F |
| 탭 이동 | Ctrl+1 … Ctrl+5 |
| 단축키 목록 | F1 |

3D 뷰에 포커스가 있으면 ← → Home End Space 도 프레임 이동/재생에 쓰입니다(목록·표에서는 방향키가 원래 기능대로 동작).
실행 취소는 **리타깃 설정과 본 매핑 편집**을 되돌립니다. 파일 열기·보기 옵션은 대상이 아닙니다. 슬라이더를 끌어서 바꾼 값은 한 번에 한 단계로 묶입니다.

메시 표시는 삼각형을 칠하는 방식이 아니라 표면을 촘촘한 점으로 찍어 음영을 주는 근사 렌더링입니다(큰 메시는 자동으로 단순화해서 재생 중에도 돌아갑니다). 닫힌 메시는 뒷면을 가리지만 열린 메시(천 등)는 가리지 않아 겹쳐 보일 수 있습니다. 작은 틈이 보일 수 있고, 최종 결과물은 내보낸 파일을 Max/Maya/Blender 에서 확인하세요.

## 사용 순서 (GUI)

1. **소스 모션 열기**(게임에서 뽑은 모션) → **타깃 모델 열기**(내 모델)
2. **1. 파일·매핑**: 자동 매핑 결과 확인. 틀린 곳, ⚠ 표시(한쪽만 연결)는 콤보박스로 고칩니다.
3. **2. 리타깃 조절**: 값을 바꾸면 바로 3D 뷰가 갱신됩니다. 파란색 = 내 모델, 주황색 = 원본 모션(겹쳐 보기), 초록 점 = 발이 고정된 곳.
4. **3. 품질 검수**: 경고를 클릭해서 문제 프레임을 확인.
5. **4. 내보내기**: 폴더, 대상 프로그램, 형식을 고르고 내보내기.
6. 마음에 드는 설정은 **프로필 저장** — 본 이름 기준이라 같은 리그에 다시 쓸 수 있습니다.

### 내보내기 옵션

| 옵션 | 설명 |
|---|---|
| 리타깃 결과 / 원본 모션 | 내 모델용 결과, 또는 리타깃 없이 원본 스켈레톤+모션 그대로(크리처/구조물) |
| 본 구조 포함 | 끄면 스켈레톤 없이 애니메이션 커브(JSON)만. 이미 같은 이름의 본이 세팅된 리그용 |
| Maya | Y-up, cm, 캐릭터가 +Z 를 바라봄, 오일러 XYZ |
| 3ds Max | Z-up, cm, 캐릭터가 −Y 를 바라봄, 오일러 ZXY |
| Blender | Y-up, m, 오일러 ZXY |
| BVH | Blender 없이 동작. 대상 프로그램별 축/단위 적용 |
| GLB | Blender 없이 동작. 메시·스킨 포함 가능 |
| FBX | **Blender 필요.** 내 모델 파일을 Blender 로 열어 모션을 키프레임으로 굽고 FBX 로 저장(메시·텍스처 포함). 리타깃 결과 전용 |

BVH 를 읽는 방식은 프로그램/버전마다 다릅니다(예: Max 는 보통 Biped 경로, Maya 는 기본 가져오기에 BVH 가 없음). 실무에서는 **FBX 가 가장 무난**합니다 — 그래서 Blender 설치를 권장합니다.
Blender 는 자동으로 찾고(PATH, `C:\Program Files\Blender Foundation\...`), 못 찾으면 내보내기 탭에서 경로를 지정하세요.

### CLI

```
python -m motionbridge retarget --source 모션.fbx --target 내모델.fbx --out 결과 ^
    --presets maya max blender --formats bvh glb fbx --clip all --save-profile 내모델.json
python -m motionbridge retarget --source 모션.fbx --what source --out 원본 --presets blender --formats glb   # 리타깃 없이 변환
python -m motionbridge retarget ... --profile 내모델.json --in-place --scale 1.1
```

## 검증 상태 (솔직하게)

**자동 테스트로 확인됨** (`python tests/run_all.py`, 전부 통과)
- 쿼터니언/오일러 수학, BVH·GLB 읽기/쓰기 왕복, 이름 기반 자동 매핑(Mixamo / UE / Biped 스타일), 좌표계 자동 인식(Y-up/Z-up, m/cm, A/T 포즈)
- 리타깃: 서로 다른 좌표계·단위·비율·T/A 포즈의 합성 휴머노이드 사이에서 본 방향 일치, 발 IK(접지 구간 미끄러짐 0%), 바닥 클램프, 골반 낮추기, 제자리 모드, 부분 매핑
- 프리셋 축/단위(Maya·Max·Blender), 본 구조 제외 모드, 프로필 저장/복원, 다중 클립 배치
- Blender 적용 스크립트의 **수학**(좌표 정합, 본 축 보정, 포즈 기저 계산)을 Blender 의 포즈 평가식으로 시뮬레이션해서 검증
- 3D 뷰어 계산(카메라/투영/장면)과 UI 로직(가짜 Qt 로 실행: 열기→매핑 편집→설정→프로필→내보내기)

**이 개발 환경에서 직접 실행하지 못한 것 — 처음 쓸 때 확인이 필요합니다**
- **추출 도우미(`mbextract`, C#)**: 이 환경에 .NET/NuGet 이 없어 **컴파일도 못 해 봤습니다.** CUE4Parse API 는 버전마다 달라서 컴파일 오류가 날 수 있습니다(오류 메시지를 알려 주면 맞춰 고칠 수 있음). Python 쪽(검사, AES 입력 게이트, 호출/프로토콜, UI)은 가짜 도우미로 시험했습니다.
- **PSA 읽기**: FModel/CUE4Parse 가 내보낸 PSA 형식(기준 포즈는 원본 값, 키는 Y 반전 + 비루트 켤레)을 소스 코드로 확인해 그대로 해석하고, 실제 Mortal Shell 2 PSA 한 개(구르기)로 사람 동작이 나오는 것까지 확인했습니다. 다른 도구가 만든 PSA 는 4가지 관례를 시험하는 대비책으로 읽습니다. 본이 200개 넘는 스켈레톤(얼굴·천 보조 본)은 3D 뷰에서 사람 슬롯에 매핑된 본만 그립니다(데이터는 전부 유지).
- **메시 추출(GLB/PSK)**: 도우미 코드는 컴파일만 확인했고 실제 게임 메시로는 아직 시험하지 못했습니다. CUE4Parse 의 GLB 를 이 프로그램이 타깃 모델로 읽는지도 실제 파일로는 미검증입니다.
- 실제 **Qt 화면**(PySide6 설치 불가). 로직은 가짜 Qt 로 검증했지만 레이아웃/실제 렌더, 그리고 단축키가 위젯 포커스와 충돌하지 않는지(Space, 숫자키 등)는 실행해 봐야 합니다. 메시 표면 렌더링은 numpy 로 이미지를 만드는 부분만 시험했습니다.
- 실제 **Blender** 에서의 FBX 읽기/내보내기 (`blender_scripts/`). 스크립트의 수학과 흐름은 시험했지만 실제 bpy 호출, Blender 버전별 옵션 이름 차이, FBX 축 옵션(`axis_forward/up`)이 Max/Maya 에서 의도대로 보이는지는 미검증입니다. 어긋나면 `core/export.py` 의 `PRESETS` 값만 바꾸면 됩니다.
- **PyInstaller / Inno Setup** 빌드 (Windows 필요).
- **실제 Mortal Shell 2 데이터**. 본 이름 규칙(트위스트/보조 본 등)이 달라 자동 매핑이 일부 틀릴 수 있습니다 — 그럴 때는 매핑 표에서 고치고 프로필로 저장하세요.

## 알려진 한계 / 다음 단계

- FBX 는 Blender 를 거칩니다. Blender 없이 쓰려면 자체 FBX 작성기가 필요합니다(아직 없음).
- 손가락은 매핑·회전 전달만 하고 IK 는 없습니다. 크리처(다리 3개 이상 등)는 발 IK 체인을 자동 구성하지 않습니다(원본 모션 내보내기만 지원).
- 리타깃은 한 클립 전체를 다시 계산하는 방식이라 아주 긴 클립에서는 슬라이더 반응이 느릴 수 있습니다.
- 프롬프트로 "느낌"을 바꾸는 기능(예: "더 무겁게", "더 빠르게")은 아직 없습니다. 지금의 조절 값(스케일, 속도, 회전 보정, 접지)을 LLM 이 고르는 파라미터 연산으로 두는 구조로 붙이는 것이 다음 단계입니다.

## 구조

```
motionbridge/core/   quat, model, io_bvh, io_gltf, mapping, retarget, quality, export, bridge(Blender), loader, pipeline
motionbridge/ui/     app(메인 창), viewer(+viewer_math), widgets, help_text
motionbridge/cli.py  명령줄
blender_scripts/     Blender 안에서 실행되는 스크립트 (convert, apply)
packaging/           PyInstaller spec, Inno Setup, build_windows.bat
tests/               run_all.py 로 전체 실행
```
