import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

let configured = false;

export function configureServerlessFontConfig(): void {
  if (configured) return;
  const moduleDirectory = dirname(fileURLToPath(import.meta.url));
  const fontDirectory = [
    join(moduleDirectory, "netlify", "functions", "_assets", "fonts"),
    join(process.cwd(), "netlify", "functions", "_assets", "fonts"),
    join(moduleDirectory, "..", "_assets", "fonts"),
    join(moduleDirectory, "_assets", "fonts"),
  ].find((candidate) => (
    existsSync(join(candidate, "fonts.conf"))
    && existsSync(join(candidate, "PretendardVariable.ttf"))
  ));
  if (!fontDirectory) {
    throw new Error("serverless_font_not_bundled");
  }
  const configFile = join(fontDirectory, "fonts.conf");
  const fontFile = join(fontDirectory, "PretendardVariable.ttf");
  if (!existsSync(configFile) || !existsSync(fontFile)) {
    throw new Error("serverless_font_not_bundled");
  }

  // sharp/libvips delegates SVG text shaping to Pango/fontconfig. Pinning the
  // directory before the dynamic sharp import prevents Lambda hosts from
  // choosing different fallback fonts or rendering Hangul as tofu boxes.
  process.env.FONTCONFIG_PATH = fontDirectory;
  process.env.FONTCONFIG_FILE = configFile;
  process.env.PANGOCAIRO_BACKEND = "fontconfig";
  configured = true;
}
