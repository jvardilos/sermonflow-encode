# TODO: don't use a generalized thing, this is a bad practice but keep things here for now

import time


def current_iso_time() -> str:
    timestamp = time.time()
    utc_struct = time.gmtime(timestamp)
    iso_string = time.strftime("%Y-%m-%dT%H:%M:%SZ", utc_struct)
    return iso_string
