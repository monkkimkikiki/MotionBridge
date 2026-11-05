"""모든 테스트 스크립트를 따로 실행하고 요약한다. 실패가 있으면 종료 코드 1."""
import glob
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
fail = []
for t in sorted(glob.glob(os.path.join(here, "test_*.py"))):
    try:
        r = subprocess.run([sys.executable, t], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired as e:  # 멈춘 시험이 CI 전체를 붙잡지 않게
        print("FAIL " + os.path.basename(t) + " (5분 안에 끝나지 않음)")
        fail.append(t)
        print(str((e.stdout or b"")[-800:]))
        continue
    ok = r.returncode == 0
    print(("PASS " if ok else "FAIL ") + os.path.basename(t))
    if not ok:
        fail.append(t)
        print((r.stdout + r.stderr)[-1500:])
print("\n%s" % ("모두 통과" if not fail else "실패 %d개" % len(fail)))
sys.exit(1 if fail else 0)
