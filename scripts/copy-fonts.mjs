import { cp, mkdir, readdir } from "node:fs/promises";
import path from "node:path";

const packages = [
  "archivo",
  "ibm-plex-sans",
  "ibm-plex-mono",
];
const destination = path.resolve("src/static/css/files");

await mkdir(destination, { recursive: true });

for (const packageName of packages) {
  const source = path.resolve("node_modules/@fontsource", packageName, "files");
  const files = await readdir(source);

  for (const file of files.filter((name) => /\.woff2?$/.test(name))) {
    await cp(path.join(source, file), path.join(destination, file));
  }
}

console.log(`Fontes copiadas para ${destination}`);
