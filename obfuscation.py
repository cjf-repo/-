from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from frames import Frame
from profiles import ProtoProfile, build_handshake_frames, default_profiles


@dataclass
class ProtoState:
    proto_id: int
    window_id: int


class ProtoObfuscator:
    def __init__(self) -> None:
        self.profiles = {profile.proto_id: profile for profile in default_profiles()}
        self.state = ProtoState(proto_id=1, window_id=0)

    def start_window(self, window_id: int, proto_id: int | None = None) -> None:
        if proto_id is None:
            proto_id = self.state.proto_id
        self.state = ProtoState(proto_id=proto_id, window_id=window_id)

    def pick_profile(self) -> ProtoProfile:
        return self.profiles[self.state.proto_id]

    def rotate_profile(self) -> None:
        ids = sorted(self.profiles)
        current_index = ids.index(self.state.proto_id)
        self.state.proto_id = ids[(current_index + 1) % len(ids)]

    def apply(self, frame: Frame) -> Frame:
        # 为当前窗口选择的模板写入 proto_id，并随机化额外头
        profile = self.pick_profile()
        frame.proto_id = profile.proto_id
        frame.extra_header = profile.pick_extra_header()
        return frame

    def encode_payload(self, frame: Frame) -> Frame:
        profile = self.profiles.get(frame.proto_id)
        if profile is None:
            return frame
        frame.payload = profile.encode_payload(frame.payload)
        return frame

    def decode_payload(self, frame: Frame) -> Frame:
        profile = self.profiles.get(frame.proto_id)
        if profile is None:
            return frame
        frame.payload = profile.decode_payload(frame.payload)
        return frame

    def handshake_frames(self, session_id: int, path_id: int) -> List[tuple[Frame, int]]:
        profile = self.pick_profile()
        return build_handshake_frames(session_id, self.state.window_id, profile, path_id)
