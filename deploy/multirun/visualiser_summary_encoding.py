"""(C) Copyright 2026, by Ross Richardson

Bounded, lossless gzip storage of canonical seed statistics with dual hashes.
Readers accept the original uncompressed receipts without rewriting them.
@author ross richardson
"""
import gzip
import hashlib
import os
import zlib

LEGACY = 'simpaths.visualiser.private-summary.v1'
COMPRESSED = 'simpaths.visualiser.private-summary.v2'
MIB = 1024**2


def payload(receipt):
    return 'metrics.json.gz' if receipt['format'] == COMPRESSED else 'metrics.json'


def stored(receipt):
    return (receipt['stored_bytes'], receipt['stored_sha256']) if receipt['format'] == COMPRESSED else (
        receipt['bytes'], receipt['sha256'])


def copy_verified(cache, directory, receipt, target=None, check=None):
    """Check both byte representations; never inflate more than the recorded bound."""
    check = check or (lambda: None)
    size = 0
    digest = hashlib.sha256()
    with cache._open(directory / payload(receipt)) as raw:
        before = os.fstat(raw.fileno())
        class Reader:
            def __init__(self):
                self.size = 0
                self.digest = hashlib.sha256()
            def read(self, count=-1):
                chunk = raw.read(count)
                self.size += len(chunk)
                if self.size > stored(receipt)[0]:
                    raise ValueError('Private summary stored size changed')
                self.digest.update(chunk)
                return chunk
        counted = Reader()
        source = gzip.GzipFile(fileobj=counted, mode='rb') if receipt['format'] == COMPRESSED else counted
        try:
            while chunk := source.read(MIB):
                check()
                size += len(chunk)
                if size > receipt['bytes']:
                    raise ValueError('Private summary expansion exceeds its bound')
                digest.update(chunk)
                if target is not None:target.write(chunk)
        except (EOFError, gzip.BadGzipFile, zlib.error) as error:
            raise ValueError('Private summary compression is corrupt') from error
        finally:
            if receipt['format'] == COMPRESSED:source.close()
        after = os.fstat(raw.fileno())
    if (size != receipt['bytes'] or digest.hexdigest() != receipt['sha256']
            or (counted.size, counted.digest.hexdigest()) != stored(receipt)
            or (before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_nlink) !=
               (after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_nlink)):
        raise ValueError('Private summary content changed')
    check()
