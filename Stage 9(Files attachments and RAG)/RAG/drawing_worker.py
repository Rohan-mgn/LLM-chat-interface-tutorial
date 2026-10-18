"""Bounded one-shot drawing worker; no database, network, or model access."""
import base64
import json
import sys
import time
from .drawing_extract import extract_page, render
from .drawing_graph import build
from .drawing_symbols import detect_tile


def main():
    request=json.loads(sys.stdin.buffer.read(65536))
    from .parser_limits import worker_limits
    worker_limits()
    try:
        op=request['operation'];path=request['path'];page=request['page']
        if op=='reference_image':
            from PIL import Image
            import io
            from .drawing_geometry import valid_box
            with Image.open(path) as image:
                if image.width*image.height>20_000_000:raise ValueError('Reference image exceeds its pixel budget.')
                bounds=request.get('bbox') or [0,0,image.width,image.height]
                if not valid_box(bounds,image.width,image.height):raise ValueError('Reference crop outside original image.')
                image=image.crop(tuple(bounds));image.thumbnail((256,256))
                if image.mode in ('RGBA','LA'):
                    rgba=image.convert('RGBA');white=Image.new('RGBA',rgba.size,'white');white.alpha_composite(rgba);image=white.convert('L')
                else:image=image.convert('L')
                image=image.point(lambda value:255 if value>=180 else 0,mode='1')
                if min(image.size)<8:raise ValueError('Reference crop is too small.')
                output=io.BytesIO();image.save(output,format='PNG')
                result={'png':base64.b64encode(output.getvalue()).decode()}
        elif op=='extract':
            result=extract_page(path,page);result['graph']=build(result)
        elif op=='render':result={'png':base64.b64encode(render(path,page,request.get('bbox'),request.get('scale',1))).decode()}
        elif op=='detect':
            png=render(path,page,request['bbox'],request['scale'])
            findings,complete=detect_tile(png,request['template'],page,request['bbox'],request['scale'],time.monotonic()+20)
            result={'findings':findings,'complete':complete}
        else:raise ValueError('Unknown drawing worker operation.')
        payload=json.dumps({'result':result},separators=(',',':'),allow_nan=False).encode()
        if len(payload)>8*1024*1024:raise ValueError('Drawing analysis output exceeds its 8 MiB budget.')
        sys.stdout.buffer.write(payload)
    except Exception as error:
        sys.stdout.write(json.dumps({'error':str(error)[:300]}))


if __name__=='__main__':main()
