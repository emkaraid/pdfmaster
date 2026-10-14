import base64, asyncio, sys, os
from playwright.async_api import async_playwright
HTML = 'file:///home/claude/pdfm/dist/PDF-мастерская.html'
T = '/home/claude/pdfm/test/'
def b64(p): return base64.b64encode(open(T + p, 'rb').read()).decode()

async def main():
    async with async_playwright() as p:
        br = await p.chromium.launch()
        pg = await br.new_page(viewport={'width': 1400, 'height': 900}, device_scale_factor=2)
        errs = []
        pg.on('console', lambda m: errs.append(f'{m.type}: {m.text}') if m.type in ('error', 'warning') else None)
        pg.on('pageerror', lambda e: errs.append('PAGEERROR ' + str(e)))
        await pg.goto(HTML)
        await pg.wait_for_function('window.__pdfm', timeout=20000)
        await pg.screenshot(path=T + 's0.png')
        # открыть документ + добавить чертёж
        await pg.evaluate('''async ([a,b]) => {
          const mk=(s,n)=>{const bin=atob(s);const u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return new File([u],n,{type:'application/pdf'})};
          await __pdfm.openFiles([mk(a,'doc.pdf')],{replace:true});
          await __pdfm.openFiles([mk(b,'drawing_r.pdf')],{replace:false});
        }''', [b64('doc.pdf'), b64('drawing_r.pdf')])
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=T + 's1.png')
        # режим правки текста, кликаем по "1 250 000"
        await pg.click('#mEdit'); await pg.wait_for_timeout(1500)
        spans = await pg.evaluate('''() => { const v=[...__pdfm.PV.values()][0]; return v.tc.items.map((it,i)=>[i,it.str]).filter(x=>x[1].trim()) }''')
        print('items', spans[:40])
        idx = next(i for i, s in spans if '250' in s)
        el = pg.locator(f'.page >> nth=0 >> .tspan[data-i="{idx}"]')
        await el.click()
        await pg.keyboard.press('Meta+a')
        txt = next(s for i, s in spans if i == idx)
        await pg.keyboard.type(txt.replace('1 250 000', '2 480 500'))
        await pg.keyboard.press('Escape')
        await pg.wait_for_timeout(300)
        await pg.screenshot(path=T + 's2.png')
        # добавить текст
        await pg.click('#mAddText')
        box = await pg.locator('.page >> nth=0').bounding_box()
        await pg.mouse.click(box['x'] + 120, box['y'] + box['height'] * 0.75)
        await pg.wait_for_timeout(200)
        await pg.keyboard.type('Принято без замечаний.\nГл. инженер Петров А.А.')
        await pg.keyboard.press('Escape')
        # печать: рисуем картинку-печать на белом фоне
        await pg.evaluate('''() => { const c=document.createElement('canvas'); c.width=400;c.height=400; const x=c.getContext('2d');
          x.fillStyle='#fff';x.fillRect(0,0,400,400); x.strokeStyle='#2244aa';x.lineWidth=10;x.beginPath();x.arc(200,200,170,0,7);x.stroke();
          x.fillStyle='#2244aa';x.font='bold 40px sans-serif';x.fillText('ПЕЧАТЬ',120,215); __pdfm.removeWhite(c); __pdfm.addLibImage(c); }''')
        await pg.click('#mStamp'); await pg.wait_for_timeout(800)
        box = await pg.locator('.page >> nth=0').bounding_box()
        await pg.mouse.click(box['x'] + box['width'] * 0.35, box['y'] + box['height'] * 0.55)
        await pg.wait_for_timeout(300)
        # тащим печать и меняем размер
        st = await pg.locator('.ov.img').bounding_box()
        await pg.mouse.move(st['x'] + st['width'] / 2, st['y'] + st['height'] / 2); await pg.mouse.down()
        await pg.mouse.move(st['x'] + st['width'] / 2 + 60, st['y'] + st['height'] / 2 + 20, steps=5); await pg.mouse.up()
        rs = await pg.locator('.ov.img .rs').bounding_box()
        await pg.mouse.move(rs['x'] + 7, rs['y'] + 7); await pg.mouse.down()
        await pg.mouse.move(rs['x'] + 47, rs['y'] + 47, steps=5); await pg.mouse.up()
        await pg.screenshot(path=T + 's3.png')
        # на 3-м листе (чертёж, повёрнут) поставить печать и поправить вертикальный текст
        await pg.click('#mSelect')
        await pg.evaluate("document.querySelectorAll('.page')[1].scrollIntoView()")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=T + 's4.png')
        await pg.click('#mEdit'); await pg.wait_for_timeout(2000)
        sp = await pg.evaluate('''() => { const v=[...__pdfm.PV.values()][1]; return v.tc ? v.tc.items.map((it,i)=>[i,it.str,it.transform]).filter(x=>x[1].trim()) : null }''')
        print('drawing items', sp)
        vi = next(i for i, s, t in sp if 'Отметка' in s)
        await pg.locator(f'.page >> nth=1 >> .tspan[data-i="{vi}"]').click()
        await pg.keyboard.press('Meta+a'); await pg.keyboard.type('Отметка верха трубы 126.10'); await pg.keyboard.press('Escape')
        await pg.click('#mStamp'); await pg.wait_for_timeout(1500)
        await pg.evaluate("document.querySelectorAll('.page')[1].scrollIntoView()"); await pg.wait_for_timeout(500)
        b2 = await pg.locator('.page >> nth=1').bounding_box()
        await pg.mouse.click(b2['x'] + 300, b2['y'] + 300)
        await pg.wait_for_timeout(800)
        await pg.screenshot(path=T + 's5.png')
        print("ovs", await pg.evaluate("__pdfm.S.overlays.map(o=>o.type+'@'+__pdfm.S.pages.findIndex(p=>p.id===o.page))"))
        # повернуть 3-й лист через миниатюру и переставить его первым
        await pg.evaluate("__pdfm.S.pages.unshift(__pdfm.S.pages.pop())")
        # сохранить
        out = await pg.evaluate('''async () => { const b = await __pdfm.buildPdf(); let s=''; for (let i=0;i<b.length;i+=8192) s+=String.fromCharCode.apply(null,b.subarray(i,i+8192)); return btoa(s); }''')
        open(T + 'out.pdf', 'wb').write(base64.b64decode(out))
        print('undo stack', await pg.evaluate('document.querySelector("#bUndo").disabled'))
        print('\n'.join(errs[:30]))
        await br.close()
asyncio.run(main())
