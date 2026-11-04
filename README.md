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
4. 검색식(정규식)으로 애니메이션을 찾고 → 고른 것을 **PSA 로 추출** → 추출된 파일을 **소스 모션으로 열기**.
   (PSA 한 파일에 스켈레톤과 모션이 함께 있어서 Blender 를 거치지 않고 바로 리타깃됩니다.)

CLI: `python -m motionbridge extract scan --game "D:\...\Sparta"` / `extract list ...` / `extract export ... --out 폴더`

실제 추출은 **추출 도우미 `mbextract`** (CUE4Parse = FModel 이 쓰는 라이브러리 기반, `tools/mbextract`)가 합니다.
빌드: .NET 10 SDK + git 이 있는 Windows 에서 `packaging\build_windows.bat`(자동) 또는
```
git clone --depth 1 --recursive https://github.com/FabianFG/CUE4Parse third_party/CUE4Parse
dotnet publish tools/mbextract -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o tools/mbextract/out
```
도우미가 없어도 나머지 기능은 모두 동작합니다.

> Mortal Shell 2 (Steam 폴더명 `Sparta`) 는 pak/IoStore 가 **암호화돼 있지 않음**을 실제 파일 헤더로 확인했습니다(AES 키 불필요). 다만 UE5 라 mappings(.usmap) 는 필요합니다.

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
- **PSA 읽기의 회전 관례**: PSA 는 도구마다 쿼터니언 켤레 관례가 달라서, 읽을 때 4가지 조합을 만들어 보고 사람 모양이 가장 잘 나오는 것을 고릅니다(합성 데이터로는 검증됨, 실제 PSA 는 미검증). 결과는 소스 파일의 "참고"에 표시되고, 모양이 이상하면 경고합니다. 루트 본 회전은 모양만으로 구분되지 않아 "위쪽이 +Z"인 쪽을 선호합니다.
- 실제 **Qt 화면**(PySide6 설치 불가). 로직은 가짜 Qt 로 검증했지만 레이아웃/실제 렌더는 실행해 봐야 합니다.
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
