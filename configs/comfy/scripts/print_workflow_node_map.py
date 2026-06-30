#!/usr/bin/env python3
"""从 Comfy Save (API Format) JSON 打印 Livehouse portrait_cartoon yaml 的 nodes 映射草稿。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python print_workflow_node_map.py /path/to/workflow_api.json")
        sys.exit(1)
    wf = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not isinstance(wf, dict):
        print("不是 API workflow 对象")
        sys.exit(1)

    loads = [k for k, v in wf.items() if v.get("class_type") == "LoadImage"]
    clips = [k for k, v in wf.items() if v.get("class_type") == "CLIPTextEncode"]
    samplers = [k for k, v in wf.items() if v.get("class_type") in ("KSampler", "SamplerCustomAdvanced")]
    ckpts = [k for k, v in wf.items() if v.get("class_type") == "CheckpointLoaderSimple"]

    print("# 粘贴到 portrait_cartoon_instantid.yaml 的 nodes:（按你的图人工核对正负 prompt）")
    if loads:
        print(f'  load_image: "{loads[0]}"' + (f"  # 另有 {loads[1:]}" if len(loads) > 1 else ""))
    if len(clips) >= 1:
        print(f'  positive_prompt: "{clips[0]}"')
    if len(clips) >= 2:
        print(f'  negative_prompt: "{clips[1]}"')
    if samplers:
        print(f'  ksampler: "{samplers[0]}"')
    if ckpts:
        print(f"# CheckpointLoaderSimple 节点: {ckpts}（Livehouse 默认只改 id=1 的 ckpt_name）")
    print("\n# 所有 LoadImage:", loads)
    print("# 所有 CLIPTextEncode:", clips)


if __name__ == "__main__":
    main()
