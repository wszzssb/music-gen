#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""moe_loadcheck.py —— **换 YMT3 权重前的加载自检**：量权重键命中率（不是"命令没报错"）。

## 为什么必须有它

`model_helper.load_model_checkpoint` 用 `model.load_state_dict(..., strict=False)` 加载 ——
**模型参数猜错时它不报错**，只是把不匹配的权重**静默丢掉**（那些子模块退回随机初始化）。
后果是"命令 rc=0、MIDI 照出、读数照有"，而模型其实是半随机的
（同族 `PITFALLS` 313/314 静默丢内容 · **200**"读数一字未变要当没生效查"）。

⇒ 换权重（尤其 MoE）前先跑本工具：**命中率 ≥0.95 才算参数对**。

## 本机实测（2026-10-06）

| 权重 | 目录（`amt/logs/2024/` 下） | 追加参数 | 命中率 |
|---|---|---|---|
| 现用（非 MoE） | `mc13_256_all_cross_v6_xk5_amp0811_edr005_attend_c_full_plus_2psn_nl26_sb_b26r_800k` | （无） | **386/386 = 1.000** |
| MoE | `mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b80_ps2` | `-ff moe -wf 4 -nmoe 8 -kmoe 2 -act silu -ln rms_norm -epe rope -rp True -rk True` | **655/655 = 1.000** |

⚠ **MoE 那条路已实测否决**（`PITFALLS` 343：逐轨精度全面更差、用户听感也否）——
本工具留着是给**以后任何**换权重用，不是让人重试 MoE。

## 用法

    <py-ml> moe_loadcheck.py --exp-dir <amt/logs/2024 下那一层目录名> [--ckpt-name model.ckpt] \
            [--extra "-ff moe -wf 4 ..."]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli_utf8 as _cu; _cu.setup()          # noqa: E402

REPO = os.environ.get('DSH_YMT3_REPO') or r'D:\test\models\ymt3repo'
LIBS = r'D:\test\models\ymt3libs'
BASE = ["-p", "2024", "-tk", "mc13_full_plus_256",
        "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf",
        "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "16"]


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--exp-dir', required=True,
                    help='amt/logs/2024/<这一层目录名>（必须与该权重配对）')
    ap.add_argument('--ckpt-name', default='model.ckpt')
    ap.add_argument('--extra', default='', help='追加的模型参数（空格分隔）')
    ap.add_argument('--repo', default=None)
    a = ap.parse_args()
    repo = a.repo or REPO
    if not os.path.isdir(repo):
        print('找不到 YourMT3 仓库：%s' % repo)
        return 2
    sys.path.insert(0, os.path.join(repo, 'amt', 'src'))
    sys.path.insert(0, repo)
    if os.path.isdir(LIBS):
        sys.path.insert(0, LIBS)      # YourMT3 要 transformers 4.x（排在 site-packages 前）
    os.chdir(repo)                    # config 里 save_dir='amt/logs' 是相对路径

    import torch
    import model_helper
    args = ["%s@%s" % (a.exp_dir, a.ckpt_name)] + BASE + [t for t in a.extra.split() if t]
    print('参数：%s ... %s' % (args[0], a.extra or '（无追加）'))
    model = model_helper.load_model_checkpoint(args=args, device='cpu')
    ckpt = os.path.join(repo, 'amt', 'logs', '2024', a.exp_dir, 'checkpoints', a.ckpt_name)
    print('权重：%s' % ckpt)
    sd = torch.load(ckpt, map_location='cpu', weights_only=False)['state_dict']
    msd = model.state_dict()
    hit = miss_shape = miss_key = 0
    bad = []
    for k, v in sd.items():
        if 'pitchshift' in k:
            continue
        if k not in msd:
            miss_key += 1
            bad.append(('模型里没有这个键', k))
            continue
        if tuple(msd[k].shape) != tuple(v.shape):
            miss_shape += 1
            bad.append(('形状 %s vs %s' % (tuple(v.shape), tuple(msd[k].shape)), k))
            continue
        hit += 1
    n = hit + miss_shape + miss_key
    print('权重键：命中 %d / %d = **%.3f** · 形状不符 %d · 模型里没有的键 %d'
          % (hit, n, hit / max(1, n), miss_shape, miss_key))
    for why, k in bad[:8]:
        print('   %-22s %s' % (why, k))
    ok = hit / max(1, n) >= 0.95
    print('⇒ %s' % ('参数对得上，可以跑' if ok else '**命中率 <0.95 ⇒ 参数猜错了，别拿它做结论**'))
    return 0 if ok else 2


if __name__ == '__main__':
    sys.exit(main())
