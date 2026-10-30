import os
import tempfile

# Los tests no tocan el libro de clientes real del usuario
os.environ.setdefault("ELGONZA_CLIENTES", os.path.join(tempfile.mkdtemp(), "clientes.txt"))
