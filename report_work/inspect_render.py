from pathlib import Path
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
import sys
sys.stdout.reconfigure(encoding='utf-8')
root=Path(__file__).resolve().parent/'render'
pdf=pdfium.PdfDocument(root/'report.pdf')
for i,page in enumerate(pdf):
    im=page.render(scale=1.6).to_pil().convert('RGB')
    im.save(root/f'page-{i+1}.png')
    tp=page.get_textpage(); text=tp.get_text_range()
    print(f'PAGE {i+1}: {text[:110].replace(chr(13)," ").replace(chr(10)," ")} | chars={len(text)}')
for start in range(0,len(pdf),6):
    sheet=Image.new('RGB',(1200,1700),'#dddddd'); draw=ImageDraw.Draw(sheet)
    for j in range(start,min(start+6,len(pdf))):
        im=Image.open(root/f'page-{j+1}.png'); im.thumbnail((390,790))
        x=((j-start)%3)*400; y=((j-start)//3)*850
        sheet.paste(im,(x,y+25)); draw.text((x+8,y+5),f'Page {j+1}',fill='black')
    sheet.save(root/f'contact-{start+1}.png')
