from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bookroll_automation.protocol import (
    ProtocolAdapter,
    cache_path,
    discover_adapter_from_bundle,
    load_cached_adapter,
    save_cached_adapter,
    viewer_script_urls,
)


SYNTHETIC_BUNDLE = '''
getBookImage",value:function(t,e){return function(){
  var r="preAA",o="tailZ",q="queryX";
  i="".concat(api,"/contents/").concat(t,"/image/").concat(e,"?").concat(q);
  s=new Function("FreshWrapper",c.data.data);
  s((function(t){var e=xo["enc"].Utf8.parse(r+c.data.iv+o),n=xo["AES"].decrypt(t,e,{mode:xo["mode"].ECB});}));
}}
'''


class ProtocolTests(unittest.TestCase):
    def test_discovers_changed_static_adapter_without_javascript_execution(self) -> None:
        adapter = discover_adapter_from_bundle(SYNTHETIC_BUNDLE)
        self.assertEqual(
            adapter,
            ProtocolAdapter(
                wrapper_name="FreshWrapper",
                key_prefix="preAA",
                image_query_key="queryX",
                key_suffix="tailZ",
            ),
        )

    def test_cache_uses_only_a_url_fingerprint(self) -> None:
        adapter = discover_adapter_from_bundle(SYNTHETIC_BUNDLE)
        base_url = "https://private.example.invalid/bookroll"
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary) / "cache"
            saved = save_cached_adapter(base_url, adapter, cache_dir)
            self.assertEqual(saved, cache_path(base_url, cache_dir))
            self.assertNotIn("private.example.invalid", saved.name)
            self.assertNotIn("private.example.invalid", saved.read_text(encoding="utf-8"))
            self.assertEqual(load_cached_adapter(base_url, cache_dir), adapter)

    def test_finds_only_application_bundle_scripts(self) -> None:
        html = '<script src="/vue/js/chunk-vendors.1.js"></script><script src="/vue/js/app.abc.js"></script>'
        self.assertEqual(viewer_script_urls(html), ["/vue/js/app.abc.js"])


if __name__ == "__main__":
    unittest.main()
