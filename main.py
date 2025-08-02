# encoding=utf-8
import copy
from collections import defaultdict
import ebooklib
import httpx
from fastapi import FastAPI, Request, HTTPException, Path
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import re
import json
from ebooklib import epub
import os
import traceback
# import fitz
from bs4 import BeautifulSoup
from bs4.element import Tag as bs4tag
# from libgen.mobi import MobiFile
import logging
logging.basicConfig(level = logging.DEBUG,format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


config = {
    "novel_dir_list": [],
    "reading_progress": {}
}


# 漫画目录路径
def load_config():
    global config
    config_path = "./config.json"
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="UTF-8") as file:
            file.write(json.dumps({"novel_dir_list": []}, ensure_ascii=False, indent=4))
    with open(config_path, "r", encoding="utf-8") as file:
        config = json.loads(file.read())


def save_config():
    global config
    with open("./config.json", "w", encoding="UTF-8") as file:
        file.write(json.dumps(config, ensure_ascii=False, indent=4))
    return True


load_config()
# 初始化FastAPI应用
app = FastAPI(title="小说阅读器", description="一个简单的小说阅读网站")
# 设置模板目录
templates = Jinja2Templates(directory="templates")
httpx_cilent = httpx.AsyncClient()
for path_name in {"cache", "file", "image"}:
    os.makedirs(f"./{path_name}", exist_ok=True)
novel_datas_path = "novel_cache.json"
novel_datas = {}
if os.path.exists(novel_datas_path):
    with open(novel_datas_path, "r", encoding="UTF-8") as file:
        novel_datas = json.loads(file.read())
novel_dir_list = {}


def extract_chapter_number(chapter_name):
    """
    从章节名称中提取数字前缀
    返回一个元组：(是否有数字前缀, 数字部分, 原始名称)
    """
    # 匹配数字前缀，如 "1. 第一章" 或 "01. Chapter 1"
    match = re.match(r'^(\d+)\.?\s*(.*)$', chapter_name)

    if match:
        # 有数字前缀，返回(是, 数字部分, 剩余部分)
        return True, int(match.group(1)), match.group(2)
    else:
        # 无数字前缀，返回(否, 一个极大的数, 原始名称)
        return False, float('inf'), chapter_name


def natural_sort_key(s):
    """用于自然排序的键函数，使1,2,10而不是1,10,2这样排序"""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def extract_images_from_epub(path: str, epub_name: str) -> list[str]:
    """从EPUB文件中提取图片，保持原有顺序"""
    logger.debug(f"提取: {epub_name}")
    try:
        path_name = epub_name.removesuffix(f".{epub_name.split('.')[-1]}")
        book = epub.read_epub(f"{path}/{epub_name}")

        # 按书籍中出现的顺序收集图片
        novel_items = {}
        section_name = "未知章节"
        os.makedirs(f"{path}/{path_name}", exist_ok=True)
        section_num = 1

        # 遍历所有项，按照spine顺序收集图片
        for num, item in enumerate(book.get_items()):
            if item.get_name().lower().endswith(('.html', '.xhtml')):
                if section_name == "未知章节" and section_name not in novel_items.keys():
                    novel_items[section_name] = []
                # 解析HTML内容
                content = item.get_content().decode('utf-8')
                # with open(f"{path}/{path_name}/content_{num}.html", "w", encoding="UTF-8") as file:
                #     file.write(content)
                soup = BeautifulSoup(content, 'lxml')
                section_list = soup.find_all("section")
                if not section_list:
                    section_list = soup.find_all("body")
                if not section_list:
                    if "<image " in str(soup):
                        image_tag = soup.find('image')
                        if image_tag:
                            href = image_tag.get('xlink:href').split('#')[0]
                            img_item = None
                            # 尝试通过href找到图片项
                            for ref_item in book.get_items():
                                if ref_item.get_type() == ebooklib.ITEM_IMAGE:
                                    # logger.info(ref_item.get_name())
                                    # logger.debug(img_href)
                                    if href.endswith(ref_item.get_name()):
                                        img_item = ref_item
                                        break
                            if img_item is not None:
                                novel_items[section_name].append({"type": "image", "data": img_item})
                            else:
                                logger.warning("图片提取错误")
                                novel_items[section_name].append({"type": "text", "data": "[图片提取错误]"})
                for section in section_list:
                    for data in section.children:
                        data_str = str(data)
                        if "</p>" in data_str:
                            text: str = str(data.text).replace("\n", "")
                            while text.startswith(" "):
                                text = text.removeprefix(" ")
                            while text.startswith(" "):
                                text = text.removeprefix(" ")
                            while text.endswith(" "):
                                text = text.removesuffix(" ")

                            CHAPTER_PATTERN = re.compile(
                                r'^(([第])'
                                r'([0-9]{1,5}|[一二三四五六七八九十百零]{1,5})'
                                r'([章卷话話幕节]))'
                            )
                            CHAPTER_PATTERN_2 = re.compile(
                                r'^(([章卷話])'
                                r'([0-9]{1,5}|[一二三四五六七八九十百零]{1,5})'
                                r'([章卷话話幕节])?)'
                            )
                            CHAPTER_PATTERN_3 = re.compile(
                                r'^(([第章卷話])?'
                                r'([0-9]{1,5}|[一二三四五六七八九十百零]{1,5})'
                                r'([章卷话話幕节])?)\s'
                            )
                            match = CHAPTER_PATTERN.match(text[:10])
                            match_2 = CHAPTER_PATTERN_2.match(text[:10])
                            match_3 = CHAPTER_PATTERN_3.match(text[:10])
                            st_text = None
                            if match:
                                st_text = match.group(1)
                            if match_2:
                                st_text = match_2.group(1)
                            if match_3:
                                st_text = match_3.group(1)
                            else:
                                for t_st in {"序章", "终章", "后记", "间章"}:
                                    if text.startswith(t_st):
                                        st_text = t_st
                                        break

                            if st_text is not None:
                                if st_text in novel_items.keys():
                                    section_num += 1
                                    section_name = f"{st_text}({section_num})"
                                else:
                                    section_name = st_text
                                if section_name not in novel_items.keys():
                                    novel_items[section_name] = []
                                novel_items[section_name].append({"type": "title", "data": st_text})

                            novel_items[section_name].append({"type": "text", "data": text})
                            # logger.info(f"文字：{text}")
                        elif "</ol>" in data_str:
                            text: str = str(data.text).replace("\n", "")
                            while text.startswith(" "):
                                text = text.removeprefix(" ")
                            while text.startswith(" "):
                                text = text.removeprefix(" ")
                            while text.endswith(" "):
                                text = text.removesuffix(" ")
                            novel_items[section_name].append({"type": "quote", "data": text})
                            # logger.info(f"文字：{text}")
                        elif "</h1>" in data_str or "</h2>" in data_str or "</b>" in data_str:
                            text: str = str(data.text).replace("\n", "")
                            while text.startswith(" "):
                                text = text.removeprefix(" ")
                            while text.startswith(" "):
                                text = text.removeprefix(" ")
                            while text.endswith(" "):
                                text = text.removesuffix(" ")
                            if text != "":
                                if section_name != text and text in novel_items.keys():
                                    section_num += 1
                                    section_name = f"{text}({section_num})"
                                else:
                                    section_name = text
                                if section_name not in novel_items.keys():
                                    novel_items[section_name] = []
                                # novel_items[section_name] = novel_items.get(section_name, [])
                            novel_items[section_name].append({"type": "title", "data": text})
                            # logger.info(f"文字：{text}")
                        elif data_str in {"\n", "</br>", "<br>"}:
                            novel_items[section_name].append({"type": "text", "data": "\n"})
                        elif data_str in {"<hr/>", "<hr>"}:
                            novel_items[section_name].append({"type": "hr", "data": "\n\n"})
                        if "<image" in data_str or "<img" in data_str:
                            image_tag = data.find('image')
                            if not image_tag:
                                image_tag = data.find('img')
                            if not image_tag:
                                logger.warning(f"没有提取到图像：{data}")
                            if image_tag and type(image_tag) is bs4tag:
                                href: str = image_tag.get('xlink:href', "").split('#')[0]
                                if href == "":
                                    href = image_tag.get('src').split('#')[0]
                                img_item = None
                                # 尝试通过href找到图片项
                                for ref_item in book.get_items():
                                    if ref_item.get_type() == ebooklib.ITEM_IMAGE:
                                        # logger.info(ref_item.get_name())
                                        # logger.debug(img_href)
                                        if href.split("/")[-1].endswith(ref_item.get_name().split("/")[-1]):
                                            img_item = ref_item
                                            break
                                if img_item is not None:
                                    novel_items[section_name].append({"type": "image", "data": img_item})
                                else:
                                    logger.warning("图片提取错误")
                                    novel_items[section_name].append({"type": "text", "data": "[图片提取错误]"})

        # 保存内容
        if novel_items:
            novel_data = {"data": {}}
            for section_name in novel_items.keys():
                for item in novel_items[section_name]:
                    if section_name not in novel_data['data'].keys():
                        novel_data['data'][section_name] = []

                    if item['type'] in {"text", "title"}:
                        novel_data['data'][section_name].append(item)
                    elif item['type'] == "image":
                        ext = item['data'].get_name().split('.')[-1]
                        if not ext or len(ext) > 5:  # 处理无效扩展名
                            ext = 'jpg'
                        # 保存图片，使用三位数编号保持顺序
                        image_name: str = item['data'].get_name()
                        image_name = image_name.split("/")[-1].removesuffix(f".{image_name.split('.')[-1]}")
                        image_path = f"{path}/{path_name}/{image_name}.{ext}"
                        with open(image_path, 'wb') as f:
                            f.write(item['data'].get_content())
                        novel_data['data'][section_name].append({"type": "image", "data": f"{image_name}.{ext}"})
                    else:
                        novel_data['data'][section_name].append(item)
            with open(f"{path}/{path_name}/content.json", "w", encoding="UTF-8") as file:
                file.write(json.dumps(novel_data, ensure_ascii=False, indent=4))

            return list(novel_data['data'].keys())
        return []
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(f"提取EPUB文件失败: {path}/{epub_name}")
        logger.error(e)
        return []


def extract_images_from_mobi(path: str, mobi_name: str):
    """从mobi文件中提取图片"""
    logger.debug(f"提取mobi：{path}/{mobi_name}")
    try:
        # 创建保存图片的目录
        path_name = mobi_name.removesuffix(f".{mobi_name.split('.')[-1]}")
        output_dir = os.path.join(path, path_name)
        os.makedirs(output_dir, exist_ok=True)

        # 读取MOBI文件
        mobi_file = os.path.join(path, mobi_name)
        with open(mobi_file, 'rb') as f:
            mobi = MobiFile(f)
            # 提取图片
            images = mobi.get_images()

            image_count = 0
            for img_id, img_data in images.items():
                image_count += 1
                # 确定图片扩展名（这里简化处理）
                ext = 'jpg'  # 实际应该根据图片数据检测
                # 保存图片
                image_path = os.path.join(output_dir, f"{image_count:03d}.{ext}")
                with open(image_path, 'wb') as img_file:
                    img_file.write(img_data)

        return image_count > 0
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(e)
        logger.error(f"提取mobi文件失败: {path}")
        return False


def extract_images_from_pdf(path: str, pdf_name: str):
    """从PDF文件中提取图片"""
    logger.debug(f"提取pdf：{path}/{pdf_name}")
    try:
        # 未编写代码
        # 创建保存图片的目录
        path_name = pdf_name.removesuffix(f".{pdf_name.split('.')[-1]}")
        output_dir = os.path.join(path, path_name)
        os.makedirs(output_dir, exist_ok=True)

        # 打开PDF文件
        pdf_path = os.path.join(path, pdf_name)
        with fitz.open(pdf_path) as doc:
            image_count = 0
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                image_list = page.get_images(full=True)

                # 处理页面中的每个图像
                for image_index, img in enumerate(image_list, start=1):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]

                    # 保存图片
                    image_count += 1
                    image_path = os.path.join(output_dir, f"{image_count:03d}.{image_ext}")
                    with open(image_path, 'wb') as img_file:
                        img_file.write(image_bytes)
        return image_count > 0
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(e)
        logger.error(f"提取PDF文件失败: {path}")
        return False


def scan_novel_directory():
    """扫描漫画目录并构建数据结构"""
    logger.debug("扫描漫画目录并构建数据结构")
    global novel_datas
    if not os.path.exists(novel_datas_path):
        with open(novel_datas_path, "w", encoding="utf-8") as file:
            file.write(json.dumps({}))
    with open(novel_datas_path, "r", encoding="utf-8") as file:
        novel_datas = json.loads(file.read())

    load_config()
    novel_list = {}
    for novel_dir in config["novel_dir_list"]:
        if not os.path.exists(novel_dir):
            os.makedirs(novel_dir)
            logger.debug(f"创建了漫画目录: {novel_dir}")
            return

        # 扫描小说目录
        for novel_name in sorted(os.listdir(novel_dir)):
            logger.info(f"加载小说：{novel_name}")
            if not os.path.isdir(f"{novel_dir}/{novel_name}"):
                continue
            novel_list[novel_name] = f"{novel_dir}/{novel_name}"
            name = novel_name
            for i in " ":
                name = name.replace(i, "")
            novel_data: dict = {
                "name": name,
                "cover": None,
                "chapters": {},
            }
            if novel_name in novel_datas.keys():
                novel_data = novel_datas[novel_name]

            # 扫描章节目录
            chapter_dirs = []
            for chapter_name in os.listdir(f"{novel_dir}/{novel_name}"):
                chapter_path = f"{novel_dir}/{novel_name}/{chapter_name}"
                if os.path.isfile(chapter_path):
                    if chapter_name.endswith(".epub"):
                        if os.path.exists(f"{novel_dir}/{novel_name}/{chapter_name.removesuffix('.epub')}"):
                            # chapter_dirs.append(chapter_name.removesuffix('.epub'))
                            continue
                        content_list = extract_images_from_epub(f"{novel_dir}/{novel_name}", chapter_name)
                        chapter_name = chapter_name.removesuffix(".epub")
                        if content_list:
                            chapter_dirs.append(chapter_name)
                    elif chapter_name.endswith(".mobi") and False:
                        if os.path.exists(f"{novel_dir}/{novel_name}/{chapter_name.removesuffix('.mobi')}"):
                            # chapter_dirs.append(chapter_name.removesuffix('.mobi'))
                            continue
                        content_list = extract_images_from_mobi(f"{novel_dir}/{novel_name}", chapter_name)
                        chapter_name = chapter_name.removesuffix(".mobi")
                        if content_list:
                            chapter_dirs.append(chapter_name)
                    elif chapter_name.endswith(".pdf"):
                        if os.path.exists(f"{novel_dir}/{novel_name}/{chapter_name.removesuffix('.pdf')}"):
                            # chapter_dirs.append(chapter_name.removesuffix('.pdf'))
                            continue
                        content_list = extract_images_from_pdf(f"{novel_dir}/{novel_name}", chapter_name)
                        chapter_name = chapter_name.removesuffix(".pdf")
                        if content_list:
                            chapter_dirs.append(chapter_name)
                    else:
                        continue
                else:
                    chapter_dirs.append(chapter_name)

            # 按照数字前缀排序章节
            # 首先按是否有数字前缀分组，然后在有数字前缀的组内按数字排序
            chapter_dirs.sort(key=extract_chapter_number)

            # 处理排序后的章节
            name_counter = defaultdict(int)  # 自动初始化计数器
            num = 0
            for chapter_dir in chapter_dirs:
                chapter_path = f"{novel_dir}/{novel_name}/{chapter_dir}"
                with open(f"{novel_dir}/{novel_name}/{chapter_dir}/content.json", "r", encoding="UTF-8") as file:
                    chapter_data = json.load(file)
                for session_name in chapter_data['data']:
                    num += 1
                    if session_name in novel_data['chapters']:
                        name_counter[session_name] += 1
                        unique_name = f"{session_name}({name_counter[session_name]})"
                    else:
                        name_counter[session_name] = 0
                        unique_name = session_name
                    novel_data['chapters'][f"{num}"] = {
                        "name": f"({chapter_dir}){unique_name}",
                        "chapter_name": session_name,
                        "chapter_path": chapter_path
                    }

            # 查找漫画封面
            cover_image = None

            # 检查是否有封面
            for format in {"jpg", "webp", "png"}:
                folder_cover_path = os.path.join(f"{novel_dir}/{novel_name}", f"folder.{format}")
                if os.path.exists(folder_cover_path):
                    cover_image = f"folder.{format}"
                    # 检查照片尺寸
                    # try:
                    #     image = Image.open(cover_image)
                    # except Exception as e:
                    #     logger.error(traceback.format_exc())
                    #     logger.error(e)
                    #     logger.error("检查尺寸错误")
                    # break

            # 如果没有folder.jpg，则使用第一章的第一张图片作为封面
            if cover_image is None and novel_data['chapters']:
                # cover_image = next(iter(novel_data['chapters'].values()))["images"][0]
                for chapter in novel_data['chapters'].keys():
                    path = f"{novel_data['chapters'][chapter]['chapter_path']}/content.json"
                    with open(path, "r", encoding="utf-8") as file:
                        chapter_datas = json.loads(file.read())
                    for session_name in chapter_datas['data']:
                        for data in chapter_datas['data'][session_name]:
                            if data['type'] == "image":
                                image_file_name = data['data']
                                cover_image = f"{chapter}/{image_file_name}"
                                break
                        if cover_image is not None:
                            break
                    if cover_image is not None:
                        break

            if novel_data['chapters'] and cover_image:
                novel_data['cover'] = cover_image
            novel_datas[novel_name] = novel_data

    # 删除多余的漫画和章节
    pop_novel_list = []
    pop_chapter_list = {}
    for novel_name in novel_datas.keys():
        if novel_name not in novel_list.keys():
            pop_novel_list.append(novel_name)
            continue

        chapter_file_list_ = os.listdir(novel_list[novel_name])
        chapter_file_list = []
        for name in chapter_file_list_:
            if os.path.isdir(f"{novel_list[novel_name]}/{name}"):
                chapter_file_list.append(name)
        chapters = {}
        for chapter in novel_datas[novel_name]['chapters'].keys():
            chapters[chapter] = novel_datas[novel_name]['chapters'][chapter]['chapter_path']

        for chapter in chapters.keys():
            if not os.path.exists(chapters[chapter]):
                logger.info(f"chapters[chapter]: '{chapter}'")
                if novel_name not in pop_chapter_list.keys():
                    pop_chapter_list[novel_name] = []
                pop_chapter_list[novel_name].append(chapter)

    for pop_novel in pop_novel_list:
        logger.warning(f"删除漫画：{pop_novel}")
        novel_datas.pop(pop_novel)
    for novel_name, chapters in pop_chapter_list.items():
        for chapter in chapters:
            logger.warning(f"删除章节：{chapter}")
            novel_datas[novel_name]['chapters'].pop(chapter)
    with open(novel_datas_path, "w", encoding="utf-8") as f:
        json.dump(novel_datas, f, ensure_ascii=False, indent=4)

    novel_dir_list = {}
    for library_path in config['novel_dir_list']:
        novel_dir_list[library_path] = os.listdir(library_path)
    logger.success(f"已扫描 {len(novel_datas)} 部小说")


# 启动时扫描漫画目录
scan_novel_directory()


# 漫画列表主页
@app.get("/", response_class=HTMLResponse)
async def novel_list(request: Request):
    novel_list = []
    for novel_name in novel_datas.keys():
        novel_list.append({
            "name": novel_datas[novel_name]['name'],
            "cover": f"/images/{novel_name}/{novel_datas[novel_name]['cover']}",
            "chapters": novel_datas[novel_name]['chapters']
        })
    return templates.TemplateResponse("index.html", {
        "request": request,
        "novel_list": novel_list,
        "title": "小说列表"
    })


# 刷新漫画列表
@app.get("/refresh", response_class=HTMLResponse)
async def refresh_novel(request: Request):
    scan_novel_directory()
    novel_list = []
    for novel_name in novel_datas.keys():
        novel_list.append({
            "name": novel_datas[novel_name]['name'],
            "cover": f"/images/{novel_name}/{novel_datas[novel_name]['cover']}",
            "chapters": novel_datas[novel_name]['chapters']
        })
    return templates.TemplateResponse("index.html", {
        "request": request,
        "novel_list": novel_list,
        "title": "小说列表"
    })


# 漫画详情页
@app.get("/novel/{novel_name}", response_class=HTMLResponse)
async def novel_detail(request: Request, novel_name: str = Path(..., title="漫画名称")):
    user_id = "user"

    novel = novel_datas.get(novel_name)
    if not novel:
        raise HTTPException(status_code=404, detail="漫画未找到")

    chapters = {}
    for chapter_id in novel['chapters'].keys():
        chapters[chapter_id] = copy.deepcopy(novel['chapters'][chapter_id])
        chapters[chapter_id]['reading_state'] = (
            config['reading_progress'].get(user_id, {}).get(novel_name, {}).get(chapter_id, 0.0))

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "novel": {
            "name": novel['name'],
            "cover": f"/images/{novel_name}/{novel['cover']}",
            "chapters": chapters
        },
        "title": novel["name"]  # 小说详情页标题
    })


# 漫画阅读页
@app.get("/novel/{novel_name}/{chapter_name}", response_class=HTMLResponse)
async def novel_read(
        request: Request,
        novel_name: str = Path(..., title="漫画名称"),
        chapter_name: str = Path(..., title="章节名称")):
    user_id = "user"

    novel = novel_datas.get(novel_name)
    if not novel:
        raise HTTPException(status_code=404, detail="漫画未找到")

    chapter = novel["chapters"].get(chapter_name)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节未找到")

    # 获取下一章和上一章
    chapter_list = list(novel["chapters"].keys())
    current_index = chapter_list.index(chapter_name)

    prev_chapter = None
    if current_index > 0:
        prev_id = chapter_list[current_index - 1]
        prev_chapter = {"id": prev_id, "name": novel["chapters"][prev_id]['name']}

    next_chapter = None
    if current_index < len(chapter_list) - 1:
        next_id = chapter_list[current_index + 1]
        next_chapter = {"id": next_id, "name": novel["chapters"][next_id]['name']}

    return templates.TemplateResponse("novel_reader.html", {
        "request": request,
        "novel_name": novel_name,
        "chapter_name": chapter_name,
        "reading_state": config['reading_progress'].get(user_id, {}).get(novel_name, {}).get(chapter_name, 0.0),
        "prev_chapter": prev_chapter,
        "next_chapter": next_chapter,
    })


# 保存阅读进度
@app.post("/api/state")
async def get_novel(novel_name: str, chapter_name: str, state: float):
    user_id = "user"

    if user_id not in config['reading_progress'].keys():
        config['reading_progress'][user_id] = {}
    if novel_name not in config['reading_progress'][user_id].keys():
        config['reading_progress'][user_id][novel_name] = {}
    # 跳过刷新后进度被覆盖
    old_state = config['reading_progress'].get(user_id, {}).get(novel_name, {}).get(chapter_name, 0.0)
    if old_state > 2 > state:
        return JSONResponse({"code": 0})
    config['reading_progress'][user_id][novel_name][chapter_name] = state
    logger.debug(f"save_state novel_name: {novel_name}, chapter_name： {chapter_name}， state： {state}")
    save_config()
    return JSONResponse({"code": 0})


# 获取阅读进度
@app.get("/api/state")
async def get_novel(novel_name: str, chapter_name: str, state: float = 0.0):
    user_id = "user"

    if user_id not in config['reading_progress'].keys():
        config['reading_progress'][user_id] = {}
    if novel_name not in config['reading_progress'][user_id].keys():
        config['reading_progress'][user_id][novel_name] = {}
    state = config['reading_progress'][user_id][novel_name].get(chapter_name, 0.0)
    logger.debug(f"get_state novel_name: {novel_name}, chapter_name： {chapter_name}， state： {state}")
    return JSONResponse({"code": 0, "data": {"state": state}})


# 获取小说内容
@app.get("/api/{novel_name}/{chapter_id}")
async def get_novel(novel_name: str, chapter_id: str):
    logger.debug(f"novel_name: {novel_name}, chapter_id: {chapter_id}")
    if not novel_name in novel_datas.keys() or chapter_id not in novel_datas[novel_name]['chapters'].keys():
        raise HTTPException(status_code=404, detail="内容未找到")
    chapter_path = novel_datas[novel_name]['chapters'][chapter_id]['chapter_path']
    if not os.path.exists(chapter_path):
        raise HTTPException(status_code=404, detail="内容未找到")
    with open(f"{chapter_path}/content.json", "r", encoding="utf-8") as file:
        chapters = json.loads(file.read())['data']

    chapter_name = novel_datas[novel_name]['chapters'][chapter_id]['chapter_name']
    chapter_data = []
    for chapter in chapters[chapter_name]:
        if chapter['type'] == "image":
            chapter_data.append({
                "type": chapter['type'],
                "data": f"/images/{novel_name}/{chapter_id}/{chapter['data']}"
            })
        else:
            chapter_data.append({
                "type": chapter['type'],
                "data": chapter['data']
            })

    return {
        "code": 0,
        "message": "",
        "data": {
            "name": novel_datas[novel_name]['name'],
            "session_name": novel_datas[novel_name]['chapters'][chapter_id]['name'],
            "cover": f"/images/{novel_name}/{novel_datas[novel_name]['cover']}",
            "content": chapter_data
        }
    }


# 获取图片
@app.get("/images/{path:path}")
async def get_image(path: str):
    path = path.replace("..", "__")

    # 检查文件扩展名
    if not path.endswith((".jpg", ".png", ".jpeg", ".tiff", ".tif", ".webp")):
        raise HTTPException(status_code=403, detail="图片文件错误")

    paths = path.split("/")
    if not paths or paths[0] not in novel_datas:
        raise HTTPException(status_code=403, detail="小说不在小说库")

    file_path = None
    if len(paths) == 2 and paths[1].startswith("folder."):
        file_path = f"{paths[0]}/{paths[1]}"
    elif len(paths) >= 3:
        # 章节图片
        if paths[1] not in novel_datas[paths[0]]['chapters']:
            raise HTTPException(status_code=403, detail="章节不在小说库")
        file_path = f"{novel_datas[paths[0]]['chapters'][paths[1]]['chapter_path']}/{paths[2]}"

    if file_path is None or not os.path.exists(file_path):
        logger.error(f"图片未找到：{path}")
        return FileResponse("./image/no_image.jpg", headers={"Cache-Control": "private"})

    return FileResponse(file_path, headers={"Cache-Control": "max-age=259200"})

