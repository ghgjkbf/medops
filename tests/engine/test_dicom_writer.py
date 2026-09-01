"""Tests for medops_engine.dicom_writer (TDD)."""

from __future__ import annotations

import pydicom
import pytest
from medops_engine.dicom_writer import validate_dicom, write_study
from pydicom.errors import InvalidDicomError


class TestWriteStudy:
    def test_writes_three_valid_ct_files(self, tmp_path) -> None:
        paths = write_study(tmp_path, 3, patient_prefix="SIM")
        assert len(paths) == 3
        for p in paths:
            assert p.exists()
            assert p.suffix == ".dcm"
            ds = pydicom.dcmread(p)
            assert ds.Modality == "CT"
            assert validate_dicom(p) is True

    def test_sop_instance_uids_are_unique(self, tmp_path) -> None:
        paths = write_study(tmp_path, 3)
        uids = {pydicom.dcmread(p).SOPInstanceUID for p in paths}
        assert len(uids) == 3

    def test_shared_study_and_series_uids(self, tmp_path) -> None:
        paths = write_study(tmp_path, 3)
        study_uids = {pydicom.dcmread(p).StudyInstanceUID for p in paths}
        series_uids = {pydicom.dcmread(p).SeriesInstanceUID for p in paths}
        assert len(study_uids) == 1
        assert len(series_uids) == 1

    def test_patient_prefix_applied(self, tmp_path) -> None:
        paths = write_study(tmp_path, 1, patient_prefix="LAB")
        ds = pydicom.dcmread(paths[0])
        assert str(ds.PatientName) == "LAB^Doe"
        assert str(ds.PatientID).startswith("LAB")

    def test_creates_outbox_dir(self, tmp_path) -> None:
        outbox = tmp_path / "nested" / "outbox"
        paths = write_study(outbox, 2)
        assert outbox.is_dir()
        assert all(p.parent == outbox for p in paths)

    def test_invalid_n_files_raises(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            write_study(tmp_path, 0)


class TestCorruption:
    def test_corrupt_files_fail_validation(self, tmp_path) -> None:
        paths = write_study(tmp_path, 3, corrupt=True)
        assert len(paths) == 3
        for p in paths:
            assert validate_dicom(p) is False
            with pytest.raises((InvalidDicomError, EOFError, Exception)):
                pydicom.dcmread(p)

    def test_corrupt_files_are_truncated(self, tmp_path) -> None:
        good_dir = tmp_path / "good"
        bad_dir = tmp_path / "bad"
        good = write_study(good_dir, 1)[0]
        bad = write_study(bad_dir, 1, corrupt=True)[0]
        assert bad.stat().st_size < good.stat().st_size * 0.5
