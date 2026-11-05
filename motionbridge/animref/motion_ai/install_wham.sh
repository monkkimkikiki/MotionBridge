#!/usr/bin/env bash
# AnimRef 모션 AI(WHAM) 설치 - WSL(Ubuntu) 안에서 실행됩니다.
#  - 여러 번 실행해도 안전합니다 (끝난 단계는 건너뜀). 중간에 실패하면 다시 실행하면 이어집니다.
#  - 로그: ~/animref_mocap/install.log  (오류가 나면 이 파일의 마지막 부분을 보여 주세요)
#  - 마지막 단계(SMPL 모델 받기)는 아이디/비밀번호를 직접 입력해야 합니다.
#
# 참고한 공식 설치 문서: https://github.com/yohanshin/WHAM/blob/main/docs/INSTALL.md
#   (공식은 conda로 PyTorch를 깔지만, 여기서는 같은 버전을 pip로 설치합니다)

export DEBIAN_FRONTEND=noninteractive
ROOT="$HOME/animref_mocap"
WHAM="$ROOT/WHAM"
CONDA_DIR="$ROOT/miniforge3"
ENV_NAME="wham"
TOTAL=8
mkdir -p "$ROOT"
LOG="$ROOT/install.log"

# 화면 + 로그 파일에 동시에 기록 (원래 화면은 3/4번으로 보관: 로그인 입력 단계에서 사용)
exec 3>&1 4>&2
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "[STEP $1/$TOTAL] $2"; }
fail() {
    echo
    echo "ANIMREF_INSTALL_FAILED step=$1 reason=$2"
    echo "-> 로그 파일: $LOG"
    exit 1
}

main() {
    echo "==== $(date '+%Y-%m-%d %H:%M:%S') AnimRef 모션 AI 설치 시작 ===="
    rm -f "$ROOT/installed.ok"

    # ------------------------------------------------------------------ 1. 시스템 도구
    step 1 "기본 도구 설치 (apt)"
    if ! command -v git >/dev/null || ! command -v wget >/dev/null || ! command -v unzip >/dev/null \
        || ! command -v gcc >/dev/null || ! dpkg -s libgl1 >/dev/null 2>&1; then
        apt-get update -y </dev/null || fail 1 "apt update 실패 (인터넷 연결 확인)"
        apt-get install -y git wget curl unzip build-essential ffmpeg libgl1 libglib2.0-0 </dev/null \
            || fail 1 "apt install 실패"
    else
        echo "이미 설치됨"
    fi

    # ------------------------------------------------------------------ 2. Miniforge (파이썬 환경 관리자)
    step 2 "Miniforge 설치 (파이썬 환경 관리 도구)"
    if [ ! -x "$CONDA_DIR/bin/conda" ]; then
        wget -q --show-progress -O "$ROOT/miniforge.sh" \
            "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh" \
            </dev/null || fail 2 "Miniforge 다운로드 실패"
        bash "$ROOT/miniforge.sh" -b -p "$CONDA_DIR" </dev/null || fail 2 "Miniforge 설치 실패"
        rm -f "$ROOT/miniforge.sh"
    else
        echo "이미 설치됨"
    fi
    # shellcheck disable=SC1091
    source "$CONDA_DIR/etc/profile.d/conda.sh" || fail 2 "conda 불러오기 실패"

    # ------------------------------------------------------------------ 3. 파이썬 3.9 환경
    step 3 "파이썬 3.9 환경 만들기 (이름: $ENV_NAME)"
    if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
        conda create -y -n "$ENV_NAME" python=3.9 </dev/null || fail 3 "환경 만들기 실패"
    else
        echo "이미 있음"
    fi
    conda activate "$ENV_NAME" || fail 3 "환경 켜기 실패"
    python -m pip install -q --upgrade "pip<24.1" </dev/null   # 오래된 패키지(mmcv 등) 설치 호환 목적

    # ------------------------------------------------------------------ 4. PyTorch 1.11 + CUDA 11.3
    step 4 "PyTorch 설치 (용량이 커서 몇 분 걸려요)"
    if ! python -c "import torch; assert torch.__version__.startswith('1.11')" 2>/dev/null; then
        python -m pip install torch==1.11.0+cu113 torchvision==0.12.0+cu113 torchaudio==0.11.0 \
            --extra-index-url https://download.pytorch.org/whl/cu113 </dev/null \
            || fail 4 "PyTorch 설치 실패"
    else
        echo "이미 설치됨"
    fi

    # ------------------------------------------------------------------ 5. WHAM 코드
    step 5 "WHAM 코드 받기"
    if [ ! -d "$WHAM/.git" ]; then
        git clone https://github.com/yohanshin/WHAM.git --recursive "$WHAM" </dev/null \
            || fail 5 "WHAM 다운로드 실패 (git clone)"
    else
        echo "이미 있음"
        (cd "$WHAM" && git submodule update --init --recursive </dev/null) || true
    fi

    # ------------------------------------------------------------------ 6. 필요한 부품
    step 6 "WHAM 부품 설치 (requirements + ViTPose)"
    cd "$WHAM" || fail 6 "WHAM 폴더 없음"
    if [ ! -f "$ROOT/.deps_ok" ]; then
        python -m pip install -r requirements.txt </dev/null || fail 6 "requirements 설치 실패"
        python -m pip install -v -e third-party/ViTPose </dev/null || fail 6 "ViTPose 설치 실패"
        touch "$ROOT/.deps_ok"
    else
        echo "이미 설치됨"
    fi
    # DPVO(카메라 이동 추적)는 설치하지 않습니다. WHAM은 없으면 '카메라 기준 동작만' 추정하도록 알아서 넘어갑니다.

    # ------------------------------------------------------------------ 7. 모델·데이터 (직접 입력)
    step 7 "AI 모델과 SMPL 인체 모델 받기 (로그인 필요)"
    if has_models; then
        echo "이미 받아 둠"
    else
        cat >&3 <<'MSG'

  ┌──────────────────────────────────────────────────────────────┐
  │ 이제 SMPL 사이트의 아이디(이메일)와 비밀번호를 물어봅니다.        │
  │  - 먼저 아래 두 사이트에 같은 아이디/비밀번호로 가입해 두세요.     │
  │      https://smpl.is.tue.mpg.de                                │
  │      https://smplify.is.tue.mpg.de                             │
  │  - 입력할 때 비밀번호가 화면에 보일 수 있으니 주변을 확인하세요.   │
  │  - SMPL은 보통 '비상업' 사용 조건에 동의하게 됩니다.              │
  └──────────────────────────────────────────────────────────────┘

MSG
        # 직접 입력해야 하므로 화면(3/4번)에 바로 연결
        (cd "$WHAM" && bash fetch_demo_data.sh) 1>&3 2>&4 || true
        if ! has_models; then
            n_ckpt=$(find "$WHAM/checkpoints" -maxdepth 1 -type f 2>/dev/null | wc -l)
            {
                echo
                echo "  ---- 무엇이 빠졌는지 ----"
                if [ -f "$WHAM/dataset/body_models/smpl/SMPL_NEUTRAL.pkl" ]; then
                    echo "  [OK]  SMPL 인체 모델"
                else
                    echo "  [없음] SMPL 인체 모델 -> 아이디/비밀번호 오류이거나, 두 사이트(smpl / smplify) 중 한 곳에 가입·이메일 확인·약관 동의가 안 된 경우예요."
                fi
                if [ "$n_ckpt" -ge 4 ]; then
                    echo "  [OK]  AI 모델 파일 ($n_ckpt개)"
                else
                    echo "  [부족] AI 모델 파일 (${n_ckpt}개, 4개 이상 필요) -> Google Drive 다운로드 제한('Too many users...')일 가능성이 커요. 몇 시간~하루 뒤 다시 실행하세요."
                fi
                echo "  위쪽에 빨간 오류가 있으면 그 줄을 캡처해 주세요."
                echo
            } >&3
            fail 7 "모델 파일이 아직 없어요. 위의 '무엇이 빠졌는지'를 확인하고, 다시 실행하면 이 단계부터 이어서 합니다."
        fi
    fi

    # ------------------------------------------------------------------ 8. 동작 확인
    step 8 "그래픽카드(CUDA) 동작 확인"
    if ! python - <<'PY'
import sys
try:
    import torch
except Exception as e:
    print("torch를 불러오지 못함:", e); sys.exit(2)
print("torch", torch.__version__, "| cuda 사용 가능:", torch.cuda.is_available())
if not torch.cuda.is_available():
    print("그래픽카드를 WSL에서 쓸 수 없어요. NVIDIA 드라이버를 최신으로 업데이트한 뒤 PC를 재부팅하고 다시 시도하세요."); sys.exit(3)
print("GPU:", torch.cuda.get_device_name())
try:
    x = torch.randn(64, 64, device="cuda"); y = (x @ x).sum().item()
    print("GPU 계산 테스트 통과")
except Exception as e:
    print("GPU 계산 실패:", e)
    print("그래픽카드가 최신이라 WHAM이 요구하는 오래된 PyTorch(1.11)와 안 맞을 수 있어요.")
    sys.exit(4)
PY
    then
        fail 8 "CUDA 확인 실패 (위 메시지 참고)"
    fi

    date '+%Y-%m-%d %H:%M:%S' > "$ROOT/installed.ok"
    echo
    echo "ANIMREF_INSTALL_DONE"
}

has_models() {
    [ -f "$WHAM/dataset/body_models/smpl/SMPL_NEUTRAL.pkl" ] \
        && [ -d "$WHAM/checkpoints" ] \
        && [ "$(find "$WHAM/checkpoints" -maxdepth 1 -type f | wc -l)" -ge 4 ]
}

main "$@"
