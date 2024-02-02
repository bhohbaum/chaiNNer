from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import numpy as np

from api import Iterator, IteratorOutputInfo
from nodes.groups import Condition, if_group
from nodes.properties.inputs import BoolInput, NumberInput, VideoFileInput
from nodes.properties.outputs import (
    AudioStreamOutput,
    DirectoryOutput,
    FileNameOutput,
    ImageOutput,
    NumberOutput,
)
from nodes.utils.utils import split_file_path

from .. import video_frames_group


@video_frames_group.register(
    schema_id="chainner:image:load_video",
    name="Load Video",
    description=[
        "Iterate over all frames in a video as images.",
        "Uses FFMPEG to read video files.",
        "This iterator is much slower than just using FFMPEG directly, so if you are doing a simple conversion, just use FFMPEG outside chaiNNer instead.",
    ],
    icon="MdVideoCameraBack",
    inputs=[
        VideoFileInput(primary_input=True),
        BoolInput("Use limit", default=False),
        if_group(Condition.bool(1, True))(
            NumberInput("Limit", default=10, minimum=1).with_docs(
                "Limit the number of frames to iterate over. This can be useful for testing the iterator without having to iterate over all frames of the video."
                " Will not copy audio if limit is used."
            )
        ),
    ],
    outputs=[
        ImageOutput("Frame Image", channels=3),
        NumberOutput(
            "Frame Index",
            output_type="if Input1 { min(uint, Input2 - 1) } else { uint }",
        ).with_docs("A counter that starts at 0 and increments by 1 for each frame."),
        DirectoryOutput("Video Directory", of_input=0),
        FileNameOutput("Name", of_input=0),
        NumberOutput("FPS"),
        AudioStreamOutput(),
    ],
    iterator_outputs=IteratorOutputInfo(outputs=[0, 1, 5]),
    kind="newIterator",
)
def load_video_node(
    path: Path,
    use_limit: bool,
    limit: int,
) -> tuple[Iterator[tuple[np.ndarray, int, tuple[list[Any], str]]], Path, str, float]:
    video_dir, video_name, _ = split_file_path(path)

    input_sws_flags = "lanczos+accurate_rnd+full_chroma_int+full_chroma_inp+bitexact"
    container = av.open(
        str(path),
        options={  # TODO: check if this is the right way to pass these flags.
            "sws_flags": input_sws_flags
        },
    )
    container.streams.video[0].thread_type = "AUTO"

    codec_context = container.streams.video[0].codec_context

    fps = codec_context.framerate or codec_context.rate
    average_rate: Fraction = container.streams.video[0].average_rate
    guessed_rate: Fraction = container.streams.video[0].guessed_rate
    base_rate: Fraction = container.streams.video[0].base_rate

    rate = average_rate or guessed_rate or base_rate

    if fps is None and rate is None:
        raise RuntimeError("Failed to get video fps")

    fps = fps or (rate.as_integer_ratio()[0] / rate.as_integer_ratio()[1])
    fps = round(fps, 2)
    frame_count = codec_context.encoded_frame_count

    duration = container.duration  # microseconds
    duration = duration / 1000000  # seconds

    if frame_count is None or frame_count == 0:
        frame_count = int(duration * fps)

    in_stream_v = container.streams.video[0]
    in_stream_a = container.streams.audio[0]

    if use_limit:
        frame_count = min(frame_count, limit)

    def iterator():
        index = 0

        audio_arr = []

        for packet in container.demux(in_stream_v, in_stream_a):
            if packet.dts is None:
                continue
            if use_limit and index >= limit:
                break

            packet_type = packet.stream.type

            for frame in packet.decode():
                if packet_type == "video":
                    in_frame = frame.to_ndarray(format="bgr24")
                    yield in_frame, index, (audio_arr, in_stream_a.codec.name)
                    index += 1
                    audio_arr = []
                elif packet_type == "audio":
                    audio_arr.append(frame)

    return (
        Iterator.from_iter(iter_supplier=iterator, expected_length=frame_count),
        video_dir,
        video_name,
        fps,
    )
