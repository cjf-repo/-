from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

from frames import Frame
from profiles import ProtoFamily, ProtoVariant, build_handshake_frames, default_profiles

# 协议混淆：基于协议族与变体对帧进行编码/解码与握手伪装。


@dataclass
class ProtoState:
    # 当前协议族
    family_id: int
    # 当前窗口 ID
    window_id: int


class ProtoObfuscator:
    def __init__(self) -> None:
        # 加载默认协议族配置
        self.families = {family.family_id: family for family in default_profiles()}
        self.state = ProtoState(family_id=1, window_id=0)
        # 路径对应的变体
        self.variant_by_path: Dict[int, int] = {}

    def start_window(
        self,
        window_id: int,
        family_by_path: Dict[int, int],
        variant_by_path: Dict[int, int],
    ) -> None:
        # 进入新窗口，更新变体映射
        self.state = ProtoState(family_id=1, window_id=window_id)
        self.variant_by_path = variant_by_path

    def pick_family(self, family_id: int) -> ProtoFamily:
        # 选择协议族
        return self.families[family_id]

    def rotate_profile(self) -> None:
        # 轮转协议族（按 id 顺序）
        ids = sorted(self.families)
        current_index = ids.index(self.state.family_id)
        self.state.family_id = ids[(current_index + 1) % len(ids)]

    def apply(self, frame: Frame, family_id: int, variant_id: int) -> Frame:
        # 为当前窗口选择的模板写入 proto_id，并随机化额外头
        family = self.pick_family(family_id)
        variant = family.variants[variant_id % len(family.variants)]
        frame.proto_id = family.family_id
        frame.extra_header = family.pick_extra_header(variant)
        return frame

    def encode_payload(self, frame: Frame, family_id: int, variant_id: int) -> Frame:
        # 编码 payload
        family = self.families.get(family_id)
        if family is None:
            return frame
        variant = family.variants[variant_id % len(family.variants)]
        frame.payload = family.encode_payload(frame.payload, variant)
        return frame

    def decode_payload(self, frame: Frame) -> Frame:
        # 解码 payload
        family = self.families.get(frame.proto_id)
        if family is None:
            return frame
        variant_id = frame.extra_header[0] if frame.extra_header else 0
        variant = family.variants[variant_id % len(family.variants)]
        frame.payload = family.decode_payload(frame.payload, variant)
        return frame

    def handshake_frames(
        self,
        session_id: int,
        path_id: int,
        family_id: int,
        variant_id: int,
    ) -> List[tuple[Frame, int]]:
        # 生成握手伪装帧序列
        family = self.pick_family(family_id)
        return build_handshake_frames(
            session_id, self.state.window_id, family, path_id, variant_id
        )
