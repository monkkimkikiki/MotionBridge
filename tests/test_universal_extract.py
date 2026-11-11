import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core.universal_extract import detect_engine, list_adapters

def test_detect_unity_markers():
    with tempfile.TemporaryDirectory() as root:
        assert detect_engine(root) == "unknown"
        with open(os.path.join(root, "UnityPlayer.dll"), "wb") as f:
            f.write(b"MZ")
        assert detect_engine(root) == "unity"
    print("universal engine detection OK")

def test_adapter_list():
    names = {x["name"] for x in list_adapters()}
    assert {"unity", "unreal"} <= names
    print("adapter registry OK")

if __name__ == "__main__":
    test_detect_unity_markers()
    test_adapter_list()
