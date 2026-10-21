# 외부 라이브러리 고지

이 저장소에는 남이 만든 파일이 `public/vendor/` 아래에 그대로 들어 있습니다. 원천징수영수증 화면(PDF 읽기 · 쪽 나누기)이 쓰며, 우리 서버에서 주고 처음 쓸 때만 불러옵니다. 우리가 고친 곳은 없습니다. 쓰는 곳과 주의는 [CLAUDE.md](CLAUDE.md)의 "급여·계약 > 원천징수영수증"을 봅니다.

| 이름 | 판 | 만든 곳 | 라이선스 | 파일 |
|---|---|---|---|---|
| pdf.js | 3.11.174 | Mozilla Foundation — https://github.com/mozilla/pdf.js | Apache License 2.0 | `public/vendor/pdfjs-3.11.174/pdf.min.js` · `pdf.worker.min.js` · `cmaps/` |
| pdf-lib | 1.17.1 | Andrew Dillon — https://github.com/Hopding/pdf-lib | MIT License | `public/vendor/pdf-lib-1.17.1/pdf-lib.min.js` |

## pdf.js 3.11.174 — Apache License 2.0

Copyright 2023 Mozilla Foundation

Apache License, Version 2.0 전문: https://www.apache.org/licenses/LICENSE-2.0 (pdf.js 저장소의 `LICENSE`: https://github.com/mozilla/pdf.js/blob/master/LICENSE). `pdf.min.js` · `pdf.worker.min.js`는 파일 맨 위의 라이선스 안내문(`@licstart` ~ `@licend`)을 그대로 두었습니다. 지우지 마세요.

`public/vendor/pdfjs-3.11.174/cmaps/`(한글 글꼴 지도 `.bcmap`)는 pdf.js 배포본(`pdfjs-dist@3.11.174`, jsDelivr)에 든 Adobe CMap 자료이며 아래 BSD 라이선스입니다.

```
%%Copyright: -----------------------------------------------------------
%%Copyright: Copyright 1990-2009 Adobe Systems Incorporated.
%%Copyright: All rights reserved.
%%Copyright:
%%Copyright: Redistribution and use in source and binary forms, with or
%%Copyright: without modification, are permitted provided that the
%%Copyright: following conditions are met:
%%Copyright:
%%Copyright: Redistributions of source code must retain the above
%%Copyright: copyright notice, this list of conditions and the following
%%Copyright: disclaimer.
%%Copyright:
%%Copyright: Redistributions in binary form must reproduce the above
%%Copyright: copyright notice, this list of conditions and the following
%%Copyright: disclaimer in the documentation and/or other materials
%%Copyright: provided with the distribution. 
%%Copyright:
%%Copyright: Neither the name of Adobe Systems Incorporated nor the names
%%Copyright: of its contributors may be used to endorse or promote
%%Copyright: products derived from this software without specific prior
%%Copyright: written permission. 
%%Copyright:
%%Copyright: THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND
%%Copyright: CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
%%Copyright: INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
%%Copyright: MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
%%Copyright: DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
%%Copyright: CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
%%Copyright: SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
%%Copyright: NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
%%Copyright: LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
%%Copyright: HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
%%Copyright: CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
%%Copyright: OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
%%Copyright: SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
%%Copyright: -----------------------------------------------------------
```

## pdf-lib 1.17.1 — MIT License

```
MIT License

Copyright (c) 2019 Andrew Dillon

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`pdf-lib.min.js` 안에는 다른 작은 라이브러리가 함께 묶여 있습니다.

- tslib (Microsoft Corporation, Apache License 2.0) — 안내문이 파일 맨 위에 그대로 있습니다.
- pako 1.0.x (Nodeca 프로젝트, MIT AND Zlib, https://github.com/nodeca/pako) — Copyright (C) 2014-2017 by Vitaly Puzrin and Andrei Tuputcyn. 위 MIT 허가 문구와 같습니다.

## 판을 올릴 때

판을 바꾸면 이 파일의 판 · 저작권 연도와 `public/vendor/` 폴더 이름, `public/index.html`의 `whLib` · `WH_PDF_OPTS`의 주소를 함께 고칩니다. pdf.js 3.11.174는 `isEvalSupported:false` 옵션으로 알려진 약점(CVE-2024-4367)을 막고 있으므로, 판을 올릴 때 그 주의를 다시 봅니다(CLAUDE.md 같은 항목).
