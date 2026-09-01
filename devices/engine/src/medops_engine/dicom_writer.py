"""DICOM file writer for the medops fault-scenario engine.

Generates minimal-but-valid CT Image Storage ``.dcm`` files with pydicom,
and optionally corrupts them (truncation) for fault-injection scenarios.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.errors import InvalidDicomError
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

_CORRUPT_KEEP_RATIO = 0.4


def _build_file_meta(sop_instance_uid: str) -> FileMetaDataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()
    return meta


def _build_dataset(
    patient_prefix: str,
    index: int,
    study_uid: str,
    series_uid: str,
    sop_instance_uid: str,
) -> FileDataset:
    now = datetime.datetime.now(tz=datetime.UTC)
    ds = FileDataset(
        filename_or_obj="",
        dataset=Dataset(),
        file_meta=_build_file_meta(sop_instance_uid),
        preamble=b"\0" * 128,
    )
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop_instance_uid
    ds.Modality = "CT"
    ds.PatientName = f"{patient_prefix}^Doe"
    ds.PatientID = f"{patient_prefix}-{index:04d}"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.StudyID = f"{patient_prefix}-STUDY"
    ds.SeriesNumber = 1
    ds.InstanceNumber = index
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.AccessionNumber = ""
    ds.Manufacturer = "medops-sim"
    ds.ManufacturerModelName = "medops-ct-sim"
    ds.BodyPartExamined = "CHEST"
    return ds


def _truncate(path: Path, keep_ratio: float = _CORRUPT_KEEP_RATIO) -> None:
    """Corrupt ``path``: truncate to ``keep_ratio`` of its size and clobber
    the 'DICM' magic prefix so ``pydicom.dcmread`` rejects the file."""
    size = path.stat().st_size
    with path.open("r+b") as f:
        f.truncate(int(size * keep_ratio))
        f.seek(128)  # DICM prefix location after the 128-byte preamble
        f.write(b"\xff\xff\xff\xff")


def write_study(
    outbox_dir: Path | str,
    n_files: int,
    patient_prefix: str = "SIM",
    corrupt: bool = False,
) -> list[Path]:
    """Write ``n_files`` CT Image Storage .dcm files into ``outbox_dir``.

    All files share one StudyInstanceUID / SeriesInstanceUID. When
    ``corrupt`` is True, each file is truncated after writing so that
    ``pydicom.dcmread`` fails on it (fault injection).
    """
    if n_files < 1:
        raise ValueError(f"n_files must be >= 1, got {n_files}")

    outbox = Path(outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)

    study_uid = generate_uid()
    series_uid = generate_uid()

    paths: list[Path] = []
    for i in range(1, n_files + 1):
        sop_instance_uid = generate_uid()
        ds = _build_dataset(patient_prefix, i, study_uid, series_uid, sop_instance_uid)
        path = outbox / f"{patient_prefix}_{i:04d}.dcm"
        ds.save_as(path, enforce_file_format=True)
        if corrupt:
            _truncate(path)
        paths.append(path)
    return paths


def validate_dicom(path: Path | str) -> bool:
    """True iff ``path`` reads back as a DICOM dataset with Modality == CT."""
    try:
        import pydicom

        ds = pydicom.dcmread(path)
    except (InvalidDicomError, EOFError, OSError, ValueError):
        return False
    return getattr(ds, "Modality", None) == "CT"
