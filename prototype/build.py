import json, base64, os
R = os.path.dirname(os.path.abspath(__file__))
NM = os.path.join(R, 'node_modules')
rd = lambda p: open(os.path.join(R, p), encoding='utf-8').read()
def js(s):  # безопасная JS-строка внутри <script>
    return json.dumps(s, ensure_ascii=True).replace('<', '\\u003c')
src = {
    'pdfjs': rd('node_modules/pdfjs-dist/build/pdf.min.mjs'),
    'worker': rd('node_modules/pdfjs-dist/build/pdf.worker.min.mjs'),
    'pdflib': rd('node_modules/pdf-lib/dist/pdf-lib.min.js'),
    'fontkit': rd('node_modules/@pdf-lib/fontkit/dist/fontkit.umd.min.js'),
    'sortable': rd('node_modules/sortablejs/Sortable.min.js'),
    'app': rd('src/app.js'),
}
F = 'node_modules/@expo-google-fonts/'
fb = lambda p: base64.b64encode(open(os.path.join(R, F + p), 'rb').read()).decode()
fonts = {
    'serif': {'r': fb('pt-serif/400Regular/PTSerif_400Regular.ttf'), 'b': fb('pt-serif/700Bold/PTSerif_700Bold.ttf')},
    'sans': {'r': fb('pt-sans/400Regular/PTSans_400Regular.ttf'), 'b': fb('pt-sans/700Bold/PTSans_700Bold.ttf')},
}
metrics = {'serif': {'asc': 1.039, 'desc': 0.286}, 'sans': {'asc': 1.018, 'desc': 0.276}}
parts = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>PDF‑мастерская</title><style>', rd('src/app.css'), '</style></head><body>',
         rd('src/body.html'),
         '<script>window.__SRC={' + ','.join(f'{k}:{js(v)}' for k, v in src.items()) + '};',
         'window.FONTDATA=' + json.dumps(fonts) + ';window.METRICS=' + json.dumps(metrics) + ';</script>',
         '<script>(0,eval)(__SRC.pdflib);(0,eval)(__SRC.fontkit);(0,eval)(__SRC.sortable);</script>',
         '''<script type="module">
const u = (s) => URL.createObjectURL(new Blob([s], { type: 'text/javascript' }));
window.pdfjsLib = await import(u(__SRC.pdfjs));
pdfjsLib.GlobalWorkerOptions.workerSrc = u(__SRC.worker);
(0, eval)(__SRC.app);
</script></body></html>''']
os.makedirs(os.path.join(R, 'dist'), exist_ok=True)
out = os.path.join(R, 'dist', 'PDF-мастерская.html')
open(out, 'w', encoding='utf-8').write(''.join(parts))
print(out, os.path.getsize(out))
