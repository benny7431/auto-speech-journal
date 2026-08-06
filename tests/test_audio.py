from __future__ import annotations

import uuid
from types import SimpleNamespace

import numpy as np
import pytest

from auto_speech_journal.audio import (
    AudioError,
    EnergyVadSegmenter,
    FlacSpool,
    SpoolLimitExceeded,
    StreamingResampler,
    WasapiMicrophone,
    default_wasapi_input_device,
    list_wasapi_input_devices,
    measure_input_level,
    resolve_wasapi_input_device,
)
from auto_speech_journal.config import DeviceFingerprint


class FakeInputStream:
    def __init__(self, *, callback, blocksize, **_kwargs):
        self.callback = callback
        self.blocksize = blocksize

    def start(self):
        samples = np.full((self.blocksize, 1), 0.25, dtype=np.float32)
        for _ in range(5):
            self.callback(samples, self.blocksize, None, None)

    def stop(self):
        return None

    def close(self):
        return None


class FakeSoundDevice:
    # The global PortAudio default intentionally points at MME. WASAPI's host
    # default below is the only value the catalog should use.
    default = SimpleNamespace(device=(1, -1))

    @staticmethod
    def query_hostapis():
        return [
            {"name": "MME", "default_input_device": 1},
            {"name": "Windows WASAPI", "default_input_device": 2},
        ]

    @staticmethod
    def query_devices():
        return [
            {
                "name": "Output only",
                "hostapi": 1,
                "max_input_channels": 0,
                "default_samplerate": 48_000,
            },
            {
                "name": "Legacy mic",
                "hostapi": 0,
                "max_input_channels": 1,
                "default_samplerate": 44_100,
            },
            {
                "name": "Microphone (Realtek(R) Audio)",
                "hostapi": 1,
                "max_input_channels": 2,
                "default_samplerate": 16_000,
            },
        ]

    @staticmethod
    def check_input_settings(**_kwargs):
        return None

    InputStream = FakeInputStream


def test_wasapi_device_resolution_uses_name_and_host_api():
    devices = list_wasapi_input_devices(FakeSoundDevice)

    assert len(devices) == 1
    assert devices[0].index == 2
    assert devices[0].is_default
    assert default_wasapi_input_device(FakeSoundDevice) == devices[0]

    resolved = resolve_wasapi_input_device(
        DeviceFingerprint(
            name="Microphone (Realtek(R) Audio)",
            host_api="Windows WASAPI",
            default_sample_rate=48_000,
            max_input_channels=2,
        ),
        FakeSoundDevice,
    )
    assert resolved.name == "Microphone (Realtek(R) Audio)"


def test_wasapi_device_resolution_rejects_duplicate_fingerprint():
    class DuplicateSoundDevice(FakeSoundDevice):
        @staticmethod
        def query_devices():
            devices = list(FakeSoundDevice.query_devices())
            devices.append(dict(devices[-1]))
            return devices

    devices = list_wasapi_input_devices(DuplicateSoundDevice)
    assert all(not device.fixed_binding_available for device in devices)
    assert all("同名" in device.binding_error for device in devices)

    with pytest.raises(AudioError, match="ambiguous"):
        resolve_wasapi_input_device(
            DeviceFingerprint(
                name="Microphone (Realtek(R) Audio)",
                host_api="Windows WASAPI",
                endpoint_id=devices[0].endpoint_id,
            ),
            DuplicateSoundDevice,
        )


def test_wasapi_endpoint_identity_does_not_persist_portaudio_indexes():
    original = list_wasapi_input_devices(FakeSoundDevice)[0]

    class ReorderedHostApis:
        @staticmethod
        def query_hostapis():
            return [
                {"name": "Windows WASAPI", "default_input_device": 1},
                {"name": "MME", "default_input_device": 0},
            ]

        @staticmethod
        def query_devices():
            return [
                {
                    "name": "Legacy mic",
                    "hostapi": 1,
                    "max_input_channels": 1,
                    "default_samplerate": 44_100,
                },
                {
                    "name": "Microphone (Realtek(R) Audio)",
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "default_samplerate": 16_000,
                },
            ]

    reordered = list_wasapi_input_devices(ReorderedHostApis)[0]

    assert original.index != reordered.index
    assert original.endpoint_id == reordered.endpoint_id
    assert original.endpoint_id == (
        "wasapi:windows wasapi:microphone (realtek(r) audio)"
    )


def test_legacy_v2_endpoint_id_falls_back_to_unique_name_and_host():
    resolved = resolve_wasapi_input_device(
        DeviceFingerprint(
            name="Microphone (Realtek(R) Audio)",
            host_api="Windows WASAPI",
            endpoint_id="wasapi:1:microphone (realtek(r) audio)",
        ),
        FakeSoundDevice,
    )

    assert resolved.index == 2
    assert resolved.endpoint_id == (
        "wasapi:windows wasapi:microphone (realtek(r) audio)"
    )


def test_wasapi_catalog_includes_virtual_inputs_and_handles_no_default():
    class VirtualSoundDevice(FakeSoundDevice):
        @staticmethod
        def query_hostapis():
            return [
                {"name": "MME", "default_input_device": 1},
                {"name": "Windows WASAPI", "default_input_device": -1},
            ]

        @staticmethod
        def query_devices():
            return [
                *FakeSoundDevice.query_devices(),
                {
                    "name": "VoiceMeeter Output",
                    "hostapi": 1,
                    "max_input_channels": 8,
                    "default_samplerate": 48_000,
                },
            ]

    devices = list_wasapi_input_devices(VirtualSoundDevice)

    assert [device.name for device in devices] == [
        "Microphone (Realtek(R) Audio)",
        "VoiceMeeter Output",
    ]
    assert not any(device.is_default for device in devices)
    with pytest.raises(AudioError, match="no default"):
        default_wasapi_input_device(VirtualSoundDevice)


def test_wasapi_default_tracks_host_api_changes():
    class SwitchingSoundDevice(FakeSoundDevice):
        default_index = 2

        @classmethod
        def query_hostapis(cls):
            return [
                {"name": "MME", "default_input_device": 1},
                {"name": "Windows WASAPI", "default_input_device": cls.default_index},
            ]

        @staticmethod
        def query_devices():
            return [
                *FakeSoundDevice.query_devices(),
                {
                    "name": "FXR-HUM-15",
                    "hostapi": 1,
                    "max_input_channels": 1,
                    "default_samplerate": 48_000,
                },
            ]

    assert default_wasapi_input_device(SwitchingSoundDevice).index == 2
    SwitchingSoundDevice.default_index = 3
    assert default_wasapi_input_device(SwitchingSoundDevice).index == 3


def test_input_level_self_test_keeps_audio_in_memory(tmp_path):
    fingerprint = DeviceFingerprint(
        name="Microphone (Realtek(R) Audio)",
        host_api="Windows WASAPI",
    )

    level = measure_input_level(
        fingerprint,
        duration_ms=500,
        sounddevice_module=FakeSoundDevice,
    )

    assert level.duration_ms == 500
    assert level.peak == pytest.approx(0.25)
    assert level.rms == pytest.approx(0.25)
    assert list(tmp_path.iterdir()) == []


def test_system_default_probe_can_open_an_ambiguous_name_by_ephemeral_index():
    class DuplicateDefaultSoundDevice(FakeSoundDevice):
        @staticmethod
        def query_devices():
            devices = list(FakeSoundDevice.query_devices())
            devices.append(dict(devices[-1]))
            return devices

    default = default_wasapi_input_device(DuplicateDefaultSoundDevice)
    assert not default.fixed_binding_available

    level = measure_input_level(
        default.fingerprint(),
        duration_ms=500,
        follow_system_default=True,
        sounddevice_module=DuplicateDefaultSoundDevice,
    )

    assert level.device.index == 2
    assert level.rms == pytest.approx(0.25)


def test_inactive_portaudio_stream_is_not_reported_as_running():
    class DeadStream:
        def __init__(self, **_kwargs):
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            return None

    class DeadSoundDevice(FakeSoundDevice):
        InputStream = DeadStream

    microphone = WasapiMicrophone(
        DeviceFingerprint(
            name="Microphone (Realtek(R) Audio)",
            host_api="Windows WASAPI",
        ),
        sounddevice_module=DeadSoundDevice,
    )
    microphone.start()
    microphone._stream.active = False

    assert not microphone.running
    with pytest.raises(AudioError, match="inactive"):
        microphone.read(timeout=0.001)
    microphone.stop()


def test_streaming_resampler_uses_injected_soxr():
    calls = []

    class FakeStream:
        def resample_chunk(self, samples, *, last):
            calls.append((samples.copy(), last))
            return samples[::3]

    class FakeSoxr:
        @staticmethod
        def ResampleStream(*args, **kwargs):
            assert args[:3] == (48_000, 16_000, 1)
            assert kwargs["quality"] == "HQ"
            return FakeStream()

    output = StreamingResampler(48_000, 16_000, soxr_module=FakeSoxr).process(
        np.arange(12, dtype=np.float32)
    )

    assert output.tolist() == [0.0, 3.0, 6.0, 9.0]
    assert calls[0][1] is False


def test_energy_vad_includes_pre_roll_and_closes_on_silence():
    vad = EnergyVadSegmenter(
        sample_rate=1_000,
        threshold=0.1,
        endpoint_silence_ms=200,
        max_segment_ms=2_000,
        pre_roll_ms=100,
        overlap_ms=0,
    )

    assert vad.accept(np.zeros(100, dtype=np.float32)) == []
    assert vad.accept(np.ones(200, dtype=np.float32)) == []
    assert vad.accept(np.zeros(100, dtype=np.float32)) == []
    completed = vad.accept(np.zeros(100, dtype=np.float32))

    assert len(completed) == 1
    assert completed[0].start_sample == 0
    assert len(completed[0].samples) == 500
    assert not completed[0].forced_endpoint


def test_flac_spool_is_atomic_and_bounded(tmp_path):
    writes = []

    def fake_writer(file, data, samplerate, *, format, subtype):
        writes.append((samplerate, format, subtype, len(data)))
        with open(file, "wb") as handle:
            handle.write(b"flac")

    spool = FlacSpool(tmp_path, limit_bytes=500, writer=fake_writer)
    path = spool.write(np.zeros(100, dtype=np.float32), segment_id="segment-1")

    assert path.name == "segment-1.flac"
    assert path.read_bytes() == b"flac"
    assert writes == [(16_000, "FLAC", "PCM_16", 100)]
    assert not list(tmp_path.glob("*.partial.flac"))

    with pytest.raises(SpoolLimitExceeded):
        FlacSpool(tmp_path, limit_bytes=100, writer=fake_writer).write(
            np.zeros(100, dtype=np.float32),
            segment_id="segment-2",
        )

    def oversized_writer(file, *_args, **_kwargs):
        with open(file, "wb") as handle:
            handle.write(b"x" * 300)

    with pytest.raises(SpoolLimitExceeded):
        FlacSpool(tmp_path / "oversized", limit_bytes=250, writer=oversized_writer).write(
            np.zeros(100, dtype=np.float32),
            segment_id="segment-3",
        )


def test_flac_spool_recovers_valid_crash_partial(tmp_path):
    segment_id = str(uuid.uuid4())
    partial = tmp_path / f".{segment_id}.nonce.partial.flac"
    partial.write_bytes(b"complete-flac")
    spool = FlacSpool(
        tmp_path,
        limit_bytes=10_000,
        reader=lambda *_args, **_kwargs: (np.ones(320, np.float32), 16_000),
    )

    recovered = spool.recover_partials()

    assert len(recovered) == 1
    assert recovered[0].segment_id == segment_id
    assert recovered[0].frame_count == 320
    assert recovered[0].path.read_bytes() == b"complete-flac"
    assert not partial.exists()


def test_flac_spool_quarantines_invalid_crash_partial(tmp_path):
    segment_id = str(uuid.uuid4())
    partial = tmp_path / f".{segment_id}.nonce.partial.flac"
    partial.write_bytes(b"truncated")

    def broken_reader(*_args, **_kwargs):
        raise RuntimeError("bad FLAC")

    spool = FlacSpool(tmp_path, limit_bytes=10_000, reader=broken_reader)
    assert spool.recover_partials() == []

    assert not partial.exists()
    assert (tmp_path / f"{partial.name}.corrupt").exists()
    assert "bad FLAC" in spool.recovery_warnings[0]


def test_disk_full_writer_keeps_nonempty_partial_for_next_start_recovery(tmp_path):
    segment_id = str(uuid.uuid4())

    def disk_full_writer(file, *_args, **_kwargs):
        with open(file, "wb") as handle:
            handle.write(b"recoverable-flac")
        raise OSError("disk full")

    spool = FlacSpool(tmp_path, limit_bytes=10_000, writer=disk_full_writer)
    with pytest.raises(OSError, match="disk full"):
        spool.write(np.ones(320, np.float32), segment_id=segment_id)

    partials = list(tmp_path.glob(f".{segment_id}.*.partial.flac"))
    assert len(partials) == 1
    recovering = FlacSpool(
        tmp_path,
        limit_bytes=10_000,
        reader=lambda *_args, **_kwargs: (np.ones(320, np.float32), 16_000),
    )
    recovered = recovering.recover_partials()

    assert len(recovered) == 1
    assert recovered[0].segment_id == segment_id
    assert recovered[0].path.read_bytes() == b"recoverable-flac"
