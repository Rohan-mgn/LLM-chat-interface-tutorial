# Browser libraries

These local files load before `script.js`, so Markdown formatting does not
need an internet connection at runtime.

| Library | Version | File | Documentation |
| --- | --- | --- | --- |
| Marked | 18.0.14 | `marked-18.0.14.umd.js` | https://marked.js.org/ |
| DOMPurify | 3.4.16 | `dompurify-3.4.16.min.js` | https://github.com/cure53/DOMPurify |

The browser builds are unmodified files from the published npm packages.
`versions.json` records their source URLs, package SHA-512 integrity values,
file SHA-256 hashes, and license identifiers. The package integrity values
were verified before copying the files. License files are included here.

When updating either dependency, download its published browser build,
verify the package integrity, update the versioned filename and script tag
in `static/index.html`, retain the license files, and rerun the Markdown
checks described in the project README.

## Prism 1.30.0

prism-1.30.0.js bundles the pinned core plus markup, CSS, C-like, JavaScript,
Python, JSON, Bash and SQL grammars from https://github.com/PrismJS/prism/tree/v1.30.0.
It runs in manual mode; response-ui.js sanitizes the highlighted output before
inserting it. Source components and SHA-256 are in versions.json; the MIT
license is in prism-LICENSE.
