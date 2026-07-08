"""pip-audit com TLS via cert store do Windows (truststore, PEP 715).

Neste ambiente o bundle do certifi não contém o CA emissor visto no caminho até
o PyPI (proxy/AV que re-assina TLS), então `python -m pip_audit` falha com
CERTIFICATE_VERIFY_FAILED. Este wrapper injeta o truststore — que valida contra
o repositório de certificados do sistema — SEM desabilitar a verificação TLS.

Uso (raiz do backend):
    .venv\\Scripts\\python.exe -m tools.pip_audit [args do pip-audit]
"""
import sys

import truststore

truststore.inject_into_ssl()

from pip_audit._cli import audit  # noqa: E402  (após o inject, por design)

if __name__ == "__main__":
    sys.exit(audit())
