"""Testes PUROS (sem DB) da sanitização de nome de arquivo — defesa contra header
splitting no Content-Disposition (BE-01/be-upload-03). Cobre tanto a função
``safe_display_filename`` quanto o validador do schema de upload."""
import pytest

from src.schemas.document_schemas import DocumentUploadSchema, safe_display_filename


def test_remove_control_chars_e_preserva_extensao():
    # CR/LF no MEIO do nome (vetor de header splitting) são removidos.
    assert safe_display_filename("contrato\r\nX-Evil: 1.pdf") == "contratoX-Evil: 1.pdf"
    # NUL e DEL removidos.
    assert safe_display_filename("a\x00b\x7fc.pdf") == "abc.pdf"
    # TAB removido.
    assert safe_display_filename("nota\tfinal.pdf") == "notafinal.pdf"


def test_reduz_a_basename_sem_componentes_de_caminho():
    assert safe_display_filename("../../etc/passwd") == "passwd"
    assert safe_display_filename("C:\\Users\\a\\nota.pdf") == "nota.pdf"
    assert safe_display_filename("pasta/sub/relatorio.pdf") == "relatorio.pdf"


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "/", "///", "\r\n", "\x00"])
def test_rejeita_vazio_ou_dots(bad):
    with pytest.raises(ValueError):
        safe_display_filename(bad)


def test_trunca_em_255():
    assert len(safe_display_filename("a" * 400 + ".pdf")) == 255


def test_schema_de_upload_sanitiza_file_name():
    doc = DocumentUploadSchema(
        case_id="case-1", file_name="rela\r\ntorio.pdf", file_type="pdf"
    )
    assert "\r" not in doc.file_name and "\n" not in doc.file_name
    assert doc.file_name == "relatorio.pdf"
