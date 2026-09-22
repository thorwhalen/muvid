# muvid.subgenres.testing

A conformance kit for subgenre plugin authors.

Ship a contract and people will implement it slightly wrong; ship a contract
*and a test that proves conformance* and they won’t. pytest does this with
`pytester`, Datasette with `datasette.utils.testing` — this is the small
version.

In your plugin’s test suite:

```default
from muvid.subgenres.testing import check_subgenre_conformance
from my_package.subgenre import MY_THING

def test_conformance(tmp_path):
    report = check_subgenre_conformance(MY_THING, workdir=tmp_path)
    assert report.ok, report.summary()
```

Pass `render=False` to check only the manifest — useful in a fast unit suite
where you don’t want to pay for a real render.

The module also provides [`echo_renderer()`](#muvid.subgenres.testing.echo_renderer), a real (if trivial) renderer
used by muvid’s own tests and by this module’s doctests.

### Functions

| [`check_subgenre_conformance`](#muvid.subgenres.testing.check_subgenre_conformance)(subgenre, \*, workdir)   | Check a manifest against the contract, and optionally run one render.   |
|------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`echo_renderer`](#muvid.subgenres.testing.echo_renderer)(request)                              | A renderer that writes its request as JSON.                             |

### Classes

| [`ConformanceReport`](#muvid.subgenres.testing.ConformanceReport)(slug[, passed, failures, ...])   | What [`check_subgenre_conformance()`](#muvid.subgenres.testing.check_subgenre_conformance) found.   |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|

### *class* muvid.subgenres.testing.ConformanceReport(slug, passed=<factory>, failures=<factory>, skipped=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What [`check_subgenre_conformance()`](#muvid.subgenres.testing.check_subgenre_conformance) found.

### muvid.subgenres.testing.check_subgenre_conformance(subgenre, , workdir, render=True, inputs=None, params=None)

Check a manifest against the contract, and optionally run one render.

* **Return type:**
  [`ConformanceReport`](#muvid.subgenres.testing.ConformanceReport)

```pycon
>>> import tempfile
>>> sg = Subgenre(slug='conformance-demo', title='Demo',
...               description='A demo.',
...               render='muvid.subgenres.testing:echo_renderer', api_versions=("1",))
>>> with tempfile.TemporaryDirectory() as d:
...     rep = check_subgenre_conformance(sg, workdir=d)
>>> rep.ok
True
```

### muvid.subgenres.testing.echo_renderer(request)

A renderer that writes its request as JSON. Not a stub — it is a real,
total implementation of the contract, which is what makes it usable as the
fixture every conformance check runs against.

* **Return type:**
  [`RenderResult`](muvid.subgenres.md#muvid.subgenres.RenderResult)
