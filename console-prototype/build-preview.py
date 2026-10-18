"""Bundle the dependency-free prototype into a portable HTML file."""

from pathlib import Path


def build() -> None:
    root = Path(__file__).resolve().parent
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="styles.css" />',
        f"<style>\n{css}\n</style>",
    )
    scripts = []
    for name in ("pages-workflow.js", "pages-admin.js", "app.js"):
        html = html.replace(f'<script src="{name}" defer></script>', "")
        source = (root / name).read_text(encoding="utf-8").replace(
            "</script", "<\\/script"
        )
        scripts.append(f"<script>\n{source}\n</script>")
    html = html.replace("</body>", "\n".join(scripts) + "\n</body>")
    target = root / "preview.html"
    target.write_text(html, encoding="utf-8")
    print(f"已生成单文件预览：{target}")


if __name__ == "__main__":
    build()
