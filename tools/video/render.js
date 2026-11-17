// Render a program video: node render.js <program_dir> [--stills t1,t2,...] [--fps 30]
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

(async () => {
  const dir = path.resolve(process.argv[2]);
  const args = process.argv.slice(3);
  const stillsArg = args.includes('--stills') ? args[args.indexOf('--stills') + 1] : null;
  const tl = JSON.parse(fs.readFileSync(path.join(dir, 'timeline.json'), 'utf8'));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('console', (m) => { if (m.type() === 'error') console.error('page:', m.text()); });
  page.on('pageerror', (e) => console.error('pageerror:', e.message));
  await page.goto('file://' + path.join(__dirname, 'engine.html'));
  const info = await page.evaluate((t) => window.init(t), tl);
  console.log('init', info);

  if (stillsArg) {
    const out = path.join(dir, 'stills'); fs.mkdirSync(out, { recursive: true });
    for (const s of stillsArg.split(',')) {
      const t = parseFloat(s);
      await page.evaluate((x) => window.setTime(x), t);
      await page.screenshot({ path: path.join(out, `t${t.toFixed(1).padStart(6, '0')}.jpg`), type: 'jpeg', quality: 85 });
    }
    await browser.close();
    console.log('stills written to', out);
    return;
  }

  const fps = tl.fps; const frames = Math.ceil(tl.total * fps);
  const outFile = path.join(dir, `${tl.program}.mp4`);
  const ff = spawn('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-',
    '-i', path.join(dir, 'narration.wav'), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', outFile], { stdio: ['pipe', 'inherit', 'inherit'] });
  const t0 = Date.now();
  for (let f = 0; f < frames; f++) {
    await page.evaluate((x) => window.setTime(x), f / fps);
    const buf = await page.screenshot({ type: 'jpeg', quality: 92 });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    if (f % 300 === 0) console.log(`frame ${f}/${frames} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
  await browser.close();
  console.log('done', outFile, ((Date.now() - t0) / 1000).toFixed(0) + 's');
})();
