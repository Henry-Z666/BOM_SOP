/**
 * Publish the water-tank SOP with the spreadsheet runtime.
 *
 * This is a product adapter: the reusable inputs are a one-main-process-per-
 * sheet template, the accepted render-job list, and one PNG per job id.  It
 * never opens Excel or controls the desktop.
 *
 * Example:
 *   node ./products/water-tank/scripts/publish_sop_spreadsheets.mjs \
 *     --reference ./data/runs/reference_template.xlsx \
 *     --output ./outputs/published_sop/water-tank-spreadsheets-trial.xlsx
 */
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve(import.meta.dirname, "../../..");
const argv = process.argv.slice(2);

function option(name, fallback) {
  const index = argv.indexOf(name);
  if (index === -1) return fallback;
  if (!argv[index + 1]) throw new Error(`Missing value for ${name}`);
  return path.resolve(argv[index + 1]);
}

const referencePath = option("--reference", process.env.SOP_REFERENCE_PATH);
if (!referencePath) {
  throw new Error("Provide --reference <one-process-per-sheet-template.xlsx> (or SOP_REFERENCE_PATH).");
}
const outputPath = option(
  "--output",
  path.join(root, "outputs", "published_sop", "JB9918900337_水箱部件装配SOP_出版版.xlsx"),
);
const jobsPath = option("--jobs", path.join(root, "data", "runs", "corrected-v2-render-jobs.json"));
const imagesDir = option("--images", path.join(root, "data", "runs", "published-sop-build", "png"));
const qaDir = path.join(path.dirname(outputPath), `${path.parse(outputPath).name}-qa`);

const pages = [
  { sheet: "第1步-固定水箱焊件", name: "固定水箱焊件", start: 0, end: 13 },
  { sheet: "第2步-安装顶板焊件", name: "安装顶板焊件", start: 13, end: 17 },
  { sheet: "第3步-安装软管", name: "安装软管", start: 17, end: 20 },
  { sheet: "第4步-安装卡式端盖", name: "安装卡式端盖", start: 20, end: 23 },
  { sheet: "第5步-安装压力传感器", name: "安装压力传感器", start: 23, end: 25 },
  { sheet: "第6步-安装球阀和转接头", name: "安装球阀和转接头", start: 25, end: 29 },
  { sheet: "第7步-安装电磁阀", name: "安装电磁阀", start: 29, end: 35 },
  { sheet: "第8步-安装进水管焊件", name: "安装进水管焊件", start: 35, end: 42 },
];

const jobs = JSON.parse(await fs.readFile(jobsPath, "utf8")).jobs;
if (jobs.length !== pages.at(-1).end) {
  throw new Error(`Expected ${pages.at(-1).end} jobs, received ${jobs.length}`);
}
for (const page of pages) {
  if (page.end <= page.start) throw new Error(`Invalid page range: ${page.sheet}`);
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(qaDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(referencePath));

function setCell(sheet, address, value) {
  sheet.getRange(address).values = [[value]];
}

for (const [index, page] of pages.entries()) {
  const sheet = workbook.worksheets.getItem(page.sheet);
  sheet.deleteAllDrawings();
  sheet.showGridLines = false;
  setCell(sheet, "B1", "温控单元 CDU600-1153L-S · 水箱部件装配作业指导书");
  setCell(sheet, "B4", "文件编号：JB9918900337-SOP-001");
  setCell(sheet, "L4", "CDU600-1153L-S");
  setCell(sheet, "S4", "—");
  setCell(sheet, "AB1", "— / 2026-08-07");
  setCell(sheet, "AB2", "—");
  setCell(sheet, "AB3", "—");
  setCell(sheet, "AN1", "A");
  setCell(sheet, "AN2", "0");
  setCell(sheet, "AN3", "—");
  setCell(sheet, "AB4", `FZ.1-${index + 1}`);
  setCell(sheet, "AB5", page.name);
  setCell(sheet, "AB6", "—");
  setCell(sheet, "AN6", "—");
}

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "SOP publication formula error scan",
});
const formulaIssues = formulaErrors.ndjson
  .split(/\r?\n/)
  .filter(Boolean)
  .map((line) => JSON.parse(line));
// Excel evaluates the existing sheet-name formula correctly. artifact-tool
// does not implement CELL("filename"), so only that known template formula is
// exempt from its own in-memory #NAME? scan; all other formula issues block.
const blockingFormulaIssues = formulaIssues.filter(
  (issue) => !(
    issue.address === "AN5"
    && issue.value === "#NAME?"
    && String(issue.formula).includes('CELL("filename"')
  ),
);
if (blockingFormulaIssues.length > 0) {
  throw new Error(`Template contains formula errors:\n${blockingFormulaIssues.map(JSON.stringify).join("\n")}`);
}

// Validate static template areas before inserting drawings. The current
// renderer cannot faithfully preview imported floating images, so the final
// workbook is also audited by drawing count and ZIP media relationships.
for (const page of pages) {
  const preview = await workbook.render({
    sheetName: page.sheet,
    range: "A1:AR45",
    scale: 0.42,
    format: "png",
  });
  await fs.writeFile(
    path.join(qaDir, `${page.sheet}.template.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const columns = [
  { col: 1, colOffsetPx: 0 },
  { col: 10, colOffsetPx: 0 },
  { col: 20, colOffsetPx: 0 },
];
const firstRow = 7;
const rowStride = 29;
const imageSize = 560;

for (const page of pages) {
  const sheet = workbook.worksheets.getItem(page.sheet);
  const pageJobs = jobs.slice(page.start, page.end);
  for (const [localIndex, job] of pageJobs.entries()) {
    const imagePath = path.join(imagesDir, `${job.job_id}.png`);
    await fs.access(imagePath);
    const imageData = await fs.readFile(imagePath);
    const gridRow = Math.floor(localIndex / columns.length);
    const gridCol = localIndex % columns.length;
    sheet.images.add({
      dataUrl: `data:image/png;base64,${imageData.toString("base64")}`,
      name: `${String(localIndex + 1).padStart(2, "0")}-${job.job_id}`,
      altText: `${page.sheet} 子步骤 ${localIndex + 1}: ${job.title}`,
      anchor: {
        from: {
          row: firstRow + gridRow * rowStride,
          col: columns[gridCol].col,
          colOffsetPx: columns[gridCol].colOffsetPx,
        },
        extent: { widthPx: imageSize, heightPx: imageSize },
      },
    });
  }
}

for (const page of pages) {
  const expected = page.end - page.start;
  const audit = await workbook.inspect({ kind: "drawing", sheetId: page.sheet, maxChars: 30000 });
  const actual = audit.ndjson.split(/\r?\n/).filter((line) => line.includes('"kind":"drawing"')).length;
  if (actual !== expected) throw new Error(`${page.sheet}: expected ${expected} drawings, received ${actual}`);
  await fs.writeFile(path.join(qaDir, `${page.sheet}.drawings.ndjson`), audit.ndjson, "utf8");
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`FINAL_XLSX=${outputPath}`);
console.log(`SHEETS=${pages.length}`);
console.log(`IMAGES=${jobs.length}`);
