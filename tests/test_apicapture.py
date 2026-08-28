# -*- coding: utf-8 -*-
"""API kaydedicisinin maskeleme testleri.

Bu qeyd faylı sonra OXUNACAQ və paylaşıla bilər. Ona görə sirlərin
yazılmadığı testlə kilidlənir — «yəqin işləyir» kifayət deyil.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arma.apicapture import (MASK, PRESENT, Recorder,      # noqa: E402
                             redact_headers, redact_value, summarize)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u"
FAKE_BLOB = "A" * 64


class TestHeaderRedaction:
    def test_cookie_value_is_never_written(self):
        out = redact_headers({"Cookie": "session=abc123; token=xyz"})
        assert out["Cookie"] == PRESENT
        assert "abc123" not in str(out)

    def test_authorization_value_is_never_written(self):
        out = redact_headers({"Authorization": "Bearer " + FAKE_JWT})
        assert out["Authorization"] == PRESENT
        assert FAKE_JWT not in str(out)

    def test_header_names_survive_so_we_know_the_auth_scheme(self):
        out = redact_headers({"Authorization": "x", "Cookie": "y"})
        assert set(out) == {"Authorization", "Cookie"}

    def test_content_type_is_kept_because_we_need_it(self):
        out = redact_headers({"Content-Type": "application/json"})
        assert out["Content-Type"] == "application/json"

    def test_set_cookie_is_masked_too(self):
        out = redact_headers({"set-cookie": "sid=deadbeef; HttpOnly"})
        assert out["set-cookie"] == PRESENT


class TestBodyRedaction:
    def test_secret_named_keys_are_masked(self):
        body = {"password": "hunter2", "api_key": "sk-abc", "csrfToken": "t"}
        out = redact_value(body)
        assert out["password"] == MASK
        assert out["api_key"] == MASK
        assert out["csrfToken"] == MASK
        assert "hunter2" not in str(out)

    def test_jwt_shaped_value_is_masked_whatever_the_key(self):
        out = redact_value({"harmless_name": FAKE_JWT})
        assert out["harmless_name"] == MASK

    def test_long_blob_is_masked_whatever_the_key(self):
        out = redact_value({"data": FAKE_BLOB})
        assert out["data"] == MASK

    def test_business_values_are_kept_we_need_them(self):
        body = {"product_id": 2579394, "price": 99.99, "qty": 10,
                "name": "Raf toster"}
        assert redact_value(body) == body

    def test_nested_secrets_are_reached(self):
        out = redact_value({"auth": {"token": "abc"}, "items": [{"secret": "s"}]})
        assert out["auth"] == MASK
        assert out["items"][0]["secret"] == MASK

    def test_long_prose_is_truncated_not_masked(self):
        out = redact_value({"note": "uzun qeyd " * 60})
        assert len(out["note"]) < 600
        assert "kəsildi" in out["note"]

    def test_long_opaque_blob_is_masked_not_merely_truncated(self):
        """500 simvolluq ayırıcısız blok sirr ola bilər — kəsmək bəs deyil."""
        assert redact_value({"note": "x" * 500})["note"] == MASK

    def test_underscore_and_camel_key_names_are_caught(self):
        """`api_key` siyahıda yox idi və MASKALANMIRDI (28.08.2026)."""
        out = redact_value({"api_key": "sk-1", "apiKey": "sk-2",
                            "API-KEY": "sk-3", "access_token": "sk-4"})
        assert all(v == MASK for v in out.values()), out

    def test_business_fields_that_merely_contain_short_words_are_kept(self):
        """«shipping» içində «pin», «author» içində «auth» var — gizlətmə."""
        body = {"shipping_address": "Baku", "author": "Ali", "keyword": "raf"}
        assert redact_value(body) == body

    def test_long_lists_keep_shape_not_everything(self):
        out = redact_value({"rows": [{"id": i} for i in range(10)]})
        assert len(out["rows"]) == 3          # 2 element + sayğac sətri
        assert "8 element daha" in str(out["rows"][-1])


class TestRecorder:
    def test_marks_group_the_timeline(self):
        rec = Recorder()
        rec.mark("addım bir")
        assert rec.entries[0]["label"] == "addım bir"

    def test_summary_says_so_when_nothing_was_captured(self):
        text = summarize([])
        assert "Heç bir XHR/fetch sorğusu tutulmadı" in text

    def test_summary_groups_by_marker_and_hides_auth_values(self):
        entries = [
            {"kind": "marker", "label": "kabinet", "at": "x"},
            {"kind": "request", "marker": "kabinet", "method": "POST",
             "url": "https://business.birmarket.az/api/search",
             "headers": {"Cookie": PRESENT, "Content-Type": "application/json"},
             "body": {"code": "2579394"}},
            {"kind": "response", "marker": "kabinet", "method": "POST",
             "url": "https://business.birmarket.az/api/search",
             "status": 200, "headers": {}, "body": {"items": []}},
        ]
        text = summarize(entries)
        assert "## kabinet" in text
        assert "POST https://business.birmarket.az/api/search" in text
        assert "Status: **200**" in text
        assert "Auth başlığı" in text
        assert "2579394" in text          # is verisi qalır
