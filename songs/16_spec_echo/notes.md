# 16_spec_echo —— 由 spec 生成的曲目（`build_song` 往返夹具）

**用途**：`t_build_song_spec_roundtrip` 需要一首"带 spec.json"的曲目当夹具 ——
本曲就是（`spec.json` 与 `song.json` 由 `build_song.py` 双向同步）。

- 生成方式：`build_song.py <spec> --out songs/16_spec_echo`（省字入口）
- 旋律画像：`refs/melody/BGM33_pyin_melody.json`
- 复现：`python scripts/make_song.py 16_spec_echo`
