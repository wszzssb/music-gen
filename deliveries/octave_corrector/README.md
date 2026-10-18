# 八度校正器交付（2026-10-18）

本目录是 `D:\test\octave-corrector\` 的入库副本（用户点名上传）。

- `REPORT.md` —— 完整报告（方法 / 五臂双尺子读数 / 假修正率 / 漏检证据 / 补漏 / 未验证项）
- `MISS-EVIDENCE.md` + `miss_runs.csv` —— 669 段漏检证据（分类 / 补法 / 阈值敏感性）
- `FILL-LOG.md` + `fill_log.csv` —— 补漏验收（逐条改动 / 读回 / 双尺子前后）
- `BGM29_ens_*.mid` —— BGM29 三来源集成基座的五臂（base/pyin/mode/learned/hybrid）+ 补漏版
- `BGM35_full_*.mid` —— BGM35 整曲的 mode/hybrid + 补漏版
- `model_*.npz` —— 小 MLP 模型（`excl_*` 是留一曲模型；`all_songs` 只给新曲用，对报告里的曲目已泄漏）
- `MANIFEST.txt` —— SHA256 清单；里面路径写的是**原始交付目录**，文件本体在本目录，hash 可直接核

口径：这是**参考引导修复**（用 bass 分轨的 pyin/CREPE/能量当证据），不是盲转录；
没有该分轨的场合不适用。工具的代码在 `scripts/octave_corrector.py`。
