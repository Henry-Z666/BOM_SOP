import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

// This is a water-tank publication adapter.  It lives in the product package
// but writes transient runs and published workbooks at the checkout root.
const root = path.resolve(import.meta.dirname, "../../..");
const referencePath = process.env.SOP_REFERENCE_PATH;
if (!referencePath) {
  throw new Error("Set SOP_REFERENCE_PATH to the one-main-process-per-sheet Excel template before publishing.");
}
const outputPath = path.join(root, "outputs", "published_sop", "JB9918900337_水箱部件装配SOP_出版版.xlsx");
const qaDir = path.join(root, "data", "runs", "published-sop-grouped", "qa");
const jobs = JSON.parse(
  await fs.readFile(path.join(root, "data", "runs", "corrected-v2-render-jobs.json"), "utf8"),
).jobs;

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

if (jobs.length !== 42) throw new Error(`Expected 42 jobs, received ${jobs.length}`);
if (pages.at(-1).end !== jobs.length) throw new Error("Page grouping does not cover every job");

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(qaDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(referencePath));

function setCell(sheet, address, value) {
  sheet.getRange(address).values = [[value]];
}

// Keep the example's BOM-derived tables and one-main-process-per-sheet layout,
// while replacing publication metadata that is known for this product.
for (const [index, page] of pages.entries()) {
  const sheet = workbook.worksheets.getItem(page.sheet);
  sheet.deleteAllDrawings();
  sheet.showGridLines = false;
  setCell(sheet, "B1", "温控单元 CDU600-1153L-S · 水箱部件装配作业指导书");
  setCell(sheet, "B4", "文件编号：JB9918900337-SOP-001");
  setCell(sheet, "L4", "CDU600-1153L-S");
  setCell(sheet, "S4", "—");
  setCell(sheet, "AB1", "— / 2026-08-06");
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
  summary: "grouped SOP formula error scan",
});
console.log(formulaErrors.ndjson);

// Text/table QA is rendered before adding images because artifact-tool's
// renderer currently omits imported floating images; exported Excel drawings
// are validated structurally after insertion.
for (const page of pages) {
  const preview = await workbook.render({
    sheetName: page.sheet,
    range: "A1:AR45",
    scale: 0.42,
    format: "png",
  });
  await fs.writeFile(
    path.join(qaDir, `${page.sheet}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

// Three-column gallery in the example's wide left-side "装配内容" area.
// The row stride keeps square images separate and preserves source pixels;
// the workbook contains one worksheet per main BOM process, not one per image.
const columns = [
  { col: 1, colOffsetPx: 0 },   // B
  { col: 10, colOffsetPx: 0 },  // K
  { col: 20, colOffsetPx: 0 },  // U (intentionally wide in the reference)
];
const firstRow = 7; // zero-based: Excel row 8
const rowStride = 29;
const imageSize = 560;

for (const page of pages) {
  const sheet = workbook.worksheets.getItem(page.sheet);
  const pageJobs = jobs.slice(page.start, page.end);
  for (const [localIndex, job] of pageJobs.entries()) {
    const gridRow = Math.floor(localIndex / columns.length);
    const gridCol = localIndex % columns.length;
    const imagePath = path.join(root, "data", "runs", "published-sop-build", "png", `${job.job_id}.png`);
    const imageData = await fs.readFile(imagePath);
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
  const drawingAudit = await workbook.inspect({
    kind: "drawing",
    sheetId: page.sheet,
    maxChars: 30000,
  });
  const count = drawingAudit.ndjson.split(/\r?\n/).filter((line) => line.includes('"kind":"drawing"')).length;
  if (count !== expected) throw new Error(`${page.sheet}: expected ${expected} drawings, received ${count}`);
  await fs.writeFile(path.join(qaDir, `${page.sheet}-drawings.ndjson`), drawingAudit.ndjson, "utf8");
  console.log(`PAGE_OK ${page.sheet}: ${count} images`);
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`FINAL_XLSX=${outputPath}`);
console.log("SHEETS=8");
console.log("IMAGES=42");
