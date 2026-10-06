"""python-mss 9.x(`mss.mss`)와 10.x(`mss.MSS`)를 모두 지원한다."""

import mss

mss_factory = getattr(mss, "MSS", None) or mss.mss
