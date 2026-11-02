# Third party components

Sopmaker's application code is MIT licensed. Its portable distribution also
contains Python and independently licensed Python packages and native libraries.
The build copies installed license and notice files into `licenses/` and records
exact package versions, source revision, and dependency-lock digest in
`build-info.json`. The dependency resolver's hashes are in the source `uv.lock`.

Principal runtime projects are [Python](https://www.python.org/),
[PyAV](https://github.com/PyAV-Org/PyAV), [FFmpeg](https://ffmpeg.org/),
[Pillow](https://github.com/python-pillow/Pillow),
[Pydantic](https://github.com/pydantic/pydantic),
[OpenAI Python](https://github.com/openai/openai-python),
[keyring](https://github.com/jaraco/keyring),
[filelock](https://github.com/tox-dev/filelock), and
[python-docx](https://github.com/python-openxml/python-docx).

PyAV wheels dynamically link FFmpeg libraries. The libraries stay as separate
files in the portable application's `_internal` directory; users can replace
them with compatible builds, including modified builds. Sopmaker imposes no
restriction on reverse engineering needed to debug such modifications. A
replacement must preserve the library ABI expected by the recorded PyAV version.

The FFmpeg vendor build is available from
[PyAV FFmpeg releases](https://github.com/PyAV-Org/pyav-ffmpeg/releases).
The build script preserves the FFmpeg source archive and its license materials
alongside the application. `native-sources/manifest.json` records the native
binary's license/configuration, the pinned vendor build recipe, and source
locations and checksums for FFmpeg and its codec dependencies. The Python
package sources are available through the
corresponding version's source distribution on PyPI. No code or media from a
private project is included.
