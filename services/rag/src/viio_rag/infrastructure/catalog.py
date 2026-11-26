"""Read-only catalog handle; document management remains in Documents."""


class ReadCatalog:
    def __init__(self, pool=None):
        self._pool = pool

    @property
    def pool(self):
        if self._pool is None:
            raise RuntimeError('Catalog unavailable')
        return self._pool

    @property
    def available(self):
        return self._pool is not None

    def set_pool(self, pool):
        self._pool = pool
