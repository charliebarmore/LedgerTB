# Third-party notices

LedgerTB is distributed under the MIT License. It also uses third-party
software under its own licenses.

## pypdfium2 and PDFium

LedgerTB uses `pypdfium2` for local PDF text extraction and page rendering.
The pypdfium2 Python bindings are available under BSD-3-Clause and Apache-2.0.
They bundle Google's PDFium library under its BSD-style license, along with
components covered by their respective permissive licenses.

Packaged LedgerTB builds include the complete license material supplied by the
installed pypdfium2 wheel in its `pypdfium2-*.dist-info/licenses` directory.
That material includes the PDFium license and the licenses for the exact PDFium
dependencies shipped for the target platform.

- pypdfium2: https://github.com/pypdfium2-team/pypdfium2
- PDFium license: https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE

Other Python dependencies retain the copyright and license terms supplied in
their package metadata and distributions.
