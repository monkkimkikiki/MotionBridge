# MotionBridge Universal Extractors

## Current support

| Adapter | Detection | Listing | Export |
|---|---|---|---|
| Unreal | Existing .pak / .utoc scanner | Existing CUE4Parse helper | Existing PSA / PSK / GLB paths |
| Unity | UnityPlayer.dll, GameAssembly.dll, global managers, *_Data folder | UnityPy object catalog | OBJ, PNG, WAV, TTF/OTF, TextAsset, raw AnimationClip payload + typetree JSON |
| Custom engines | Plug-in entry point | Adapter-defined | Adapter-defined |

This is an extensible adapter framework, not a claim that every commercial game's private format is universally readable. A custom engine requires a parser for its container, compression, serialization, and asset schema.

## Desktop UI

Open MotionBridge and select the **Unity** tab beside the existing extraction tab. Choose the Unity game installation folder (or its `*_Data` directory), optionally enter a UnityPy-supported AssetBundle key, scan, filter/select assets, and export. The key is kept in the current UI session and is not written to the application settings file. The output folder defaults to `~/MotionBridge_extracted/Unity`.

## CLI

Install the Unity extra:

    pip install -e ".[unity]"

Detect the engine:

    python -m motionbridge universal scan --game "D:\Games\Example"
    python -m motionbridge adapters

List assets:

    python -m motionbridge universal list --game "D:\Games\Example" --engine auto

Export supported Unity objects:

    python -m motionbridge universal export --game "D:\Games\Example" --engine unity --out extracted

## Custom adapter contract

Publish a Python package with this entry point:

    [project.entry-points."motionbridge.extractors"]
    myengine = "myengine.motionbridge_adapter:create_adapter"

The factory returns an object with:
- description: str (optional)
- detect(root: str) -> bool (optional; used for auto-detection)
- scan(root: str, aes=None, mapping=None, bundle_key=None, on_event=None) -> list[dict]
- export(root: str, out_dir: str, **options) -> list[str] (options may include aes, mapping, bundle_key, path_ids, on_event)

The universal CLI passes adapter-specific options through to the adapter. Adapters must only process local files the operator is authorized to inspect. They must not circumvent DRM, anti-cheat, online authorization, or access controls.

## Keys and mapping files

AES keys and mapping files are format-specific inputs, not universal decryption instructions. The existing Unreal path accepts AES-256 and .usmap through CUE4Parse. UnityPy handles supported Unity serialized files and bundles directly; many Unity games do not use an external .usmap. A proprietary/custom engine adapter may define its own key and mapping options. UnityPy also exposes a user-supplied Unity AssetBundle decryption key for supported Unity bundle encryption; this is not a generic AES-key interface. Supplying a key alone cannot identify an unknown cipher, KDF, compression scheme, or object serialization.

## Known limitations

- Unity Mesh exports are OBJ and do not preserve a complete skinned rig. The renderer export path is documented by UnityPy as alpha-version.
- Unity AnimationClip data is preserved as a raw serialized payload with a typetree JSON sidecar; it is not yet decoded to FBX/PSA or retarget-ready motion.
- Unity games that wrap assets in unsupported custom encryption/compression need a dedicated adapter.
- Detection is heuristic. Use --engine unity or an installed adapter name when auto-detection is inconclusive.
