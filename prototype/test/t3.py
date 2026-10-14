import base64, asyncio
from playwright.async_api import async_playwright
HTML='file:///home/claude/pdfm/dist/PDF-мастерская.html'; T='/home/claude/pdfm/test/'
b64=lambda p: base64.b64encode(open(T+p,'rb').read()).decode()
async def main():
  async with async_playwright() as p:
    br=await p.chromium.launch(); pg=await br.new_page(viewport={'width':1400,'height':900})
    pg.on('pageerror', lambda e: print('ERR',e))
    pg.on('console', lambda m: print('C',m.text) if m.type=='error' else None)
    await pg.goto(HTML); await pg.wait_for_function('window.__pdfm')
    await pg.screenshot(path=T+'e0.png')
    # открыть через input
    await pg.set_input_files('#fOpen', T+'drawing.pdf'); await pg.wait_for_timeout(1500)
    await pg.set_input_files('#fAdd', [T+'doc.pdf']); await pg.wait_for_timeout(1500)
    ids=lambda: pg.evaluate("__pdfm.S.pages.map(p=>p.src.slice(0,3)+p.idx+'r'+p.rot).join(' ')")
    print('start', await ids())
    t=pg.locator('.thumb >> nth=0'); await t.hover(); await pg.locator('.thumb >> nth=0 >> button[data-a=rot]').click(); await pg.wait_for_timeout(500)
    print('rot', await ids())
    # перетащить 3-й наверх
    a=await pg.locator('.thumb >> nth=2').bounding_box(); b=await pg.locator('.thumb >> nth=0').bounding_box()
    await pg.mouse.move(a['x']+80,a['y']+60); await pg.mouse.down(); await pg.mouse.move(a['x']+80,a['y']+40,steps=3)
    await pg.mouse.move(b['x']+80,b['y']+20,steps=12); await pg.mouse.up(); await pg.wait_for_timeout(600)
    print('drag', await ids())
    await pg.click('#bUndo'); await pg.wait_for_timeout(500); print('undo', await ids())
    await pg.click('#bUndo'); await pg.wait_for_timeout(500); print('undo2', await ids())
    await pg.click('#bRedo'); await pg.wait_for_timeout(500); print('redo', await ids())
    # выбор чекбоксами и удаление
    await pg.locator('.thumb >> nth=1 >> .chk').check(); await pg.wait_for_timeout(200)
    await pg.click('#sDel'); await pg.wait_for_timeout(500); print('del', await ids())
    # подпись
    await pg.click('#mStamp'); await pg.click('#bDraw')
    c=await pg.locator('#sigc').bounding_box()
    await pg.mouse.move(c['x']+50,c['y']+150); await pg.mouse.down()
    import math
    for i in range(60): await pg.mouse.move(c['x']+50+i*7, c['y']+110+40*math.sin(i/4))
    await pg.mouse.up(); await pg.screenshot(path=T+'e1.png')
    await pg.click('#sigOk'); await pg.wait_for_timeout(300)
    # загрузка «скана» печати JPG
    from PIL import Image, ImageDraw, ImageFilter
    im=Image.new('RGB',(500,500),(244,241,236)); d=ImageDraw.Draw(im); d.ellipse((40,40,460,460),outline=(40,70,170),width=12); d.text((180,240),'OOO URO',fill=(40,70,170))
    im=im.filter(ImageFilter.GaussianBlur(1)); im.save(T+'stamp.jpg',quality=80)
    await pg.set_input_files('#fImg', T+'stamp.jpg'); await pg.wait_for_timeout(800)
    await pg.screenshot(path=T+'e2.png')
    await br.close()
asyncio.run(main())
