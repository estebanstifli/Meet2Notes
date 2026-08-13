# Meet2Notes landing page

This directory is the static GitHub Pages site for Meet2Notes. It deliberately
uses plain HTML, CSS and JavaScript: there is no build step, package manager,
analytics service or runtime dependency.

## Publishing

`.github/workflows/pages.yml` uploads this directory whenever a landing-page
change reaches `main`. In the GitHub repository, set **Settings → Pages → Build
and deployment → Source** to **GitHub Actions** once. The project site will then
be available at `https://estebanstifli.github.io/Meet2Notes/`.

`meet2notes.github.io` is only available to a GitHub user or organization named
`meet2notes`. A separate custom domain can also be configured in the repository
Pages settings; adding a `CNAME` file alone is not sufficient.

## Adding the real demo media

- Replace the `.video-placeholder` block in `index.html` with a privacy-enhanced
  YouTube embed or a linked thumbnail. Keep a descriptive `title` on the iframe.
- Replace each `.shot-placeholder` with a `<figure>` containing an optimized
  WebP/AVIF screenshot, useful `alt` text and an optional caption.
- Put landing-only media under `landing/assets/`. Do not reference private test
  recordings or the application data directory.
- Keep screenshots below roughly 300 KB when practical and provide explicit
  image dimensions to prevent layout shift.

The generated Open Graph card is `assets/og-meet2notes.png`. Update both its
Open Graph and X metadata URLs in `index.html` if the Pages domain changes.
