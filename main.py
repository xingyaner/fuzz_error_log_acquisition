import sys
import re
import urllib.request
import ssl
from typing import List
from bs4 import BeautifulSoup
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os


class Tee:
    """同时输出到控制台和文件的类"""

    def __init__(self, filename):
        self.file = open(filename, 'w', encoding='utf-8')
        self.stdout = sys.stdout
        self.stderr = sys.stderr
        sys.stdout = self
        sys.stderr = self

    def write(self, message):
        # 输出到控制台
        self.stdout.write(message)
        # 写入文件
        self.file.write(message)
        # 立即刷新缓冲区
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        # 恢复原始输出流
        sys.stdout = self.stdout
        sys.stderr = self.stderr
        # 关闭文件
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def expand_shadow_dom(driver):
    """递归展开页面中的所有Shadow DOM"""
    driver.execute_script("""
        function expandShadowRoots(root) {
            root.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot) {
                    const container = document.createElement('div');
                    container.className = '__shadow_contents';
                    container.innerHTML = el.shadowRoot.innerHTML;
                    el.appendChild(container);
                    expandShadowRoots(container);
                    }
                });
        }
        // 从 document.body 开始
        expandShadowRoots(document.body);
    """)
    print("🔍 Shadow DOM已展平")


def expand_shadow_dom_with_timeout(driver, timeout=3):
    """递归展开页面中的所有Shadow DOM，但最多执行指定秒数"""
    start_time = time.time()

    # 定义展开函数
    expand_js = """
    function expandShadowRoots(root) {
        const elements = Array.from(root.querySelectorAll('*'));
        let count = 0;

        for (const el of elements) {
            if (el.shadowRoot && !el.shadowRoot.__expanded) {
                const container = document.createElement('div');
                container.className = '__shadow_contents';
                container.innerHTML = el.shadowRoot.innerHTML;
                el.appendChild(container);
                el.shadowRoot.__expanded = true;
                count++;

                // 递归展开新添加的内容
                count += expandShadowRoots(container);
            }
        }
        return count;
    }

    // 从 document.body 开始
    return expandShadowRoots(document.body);
    """

    print(f"⏱️ 开始展平Shadow DOM，最多等待{timeout}秒...")

    # 使用循环逐步展开，而不是一次性执行
    while time.time() - start_time < timeout:
        cnt = driver.execute_script(expand_js)
        if cnt == 0:
            print("✅ Shadow DOM已完全展平")
            return
        time.sleep(0.1)  # 短暂暂停避免过度占用CPU

    print(f"⏱️ 时间到，已展平部分Shadow DOM")


def extract_build_log_urls(chromedriver_path, url, combined, mark):
    """
    从combined列表处理按钮点击并提取日志URL
    参数:
        chromedriver_path: ChromeDriver可执行文件路径
        url: 目标网页URL
        combined: 包含(index, timestamp, status)的列表
        mark: 标记列表，用于过滤不需要处理的按钮
    返回:
        log_url_list: 日志URL列表
        date_and_state_list: 日期和状态列表
    """
    log_url_list = []
    date_and_state_list = []

    for index, timestamp, status in combined:
        if mark[index] == 3:  # 跳过不需要的按钮
            continue

        driver = None
        try:
            # 初始化ChromeDriver
            opts = Options()
            opts.add_argument("--disable-gpu")
            opts.add_argument("--no-sandbox")
            opts.add_argument(
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
            )
            service = Service(executable_path=chromedriver_path)
            driver = webdriver.Chrome(service=service, options=opts)

            # 访问URL
            driver.get(url)

            # 等待 build-status 出现并异步加载完毕
            WebDriverWait(driver, 100).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
            )
            time.sleep(20)

            # 初始展开Shadow DOM
            expand_shadow_dom(driver)

            # 提取日期部分 (精确到天)
            date_part = timestamp.split()[0].replace("/", "_")
            status_str = "success" if status == 1 else "error"

            # 点击按钮
            print(f"🖱️ 点击按钮 #{index} ({timestamp}, {status_str})...")
            max_retries = 2  # 最大重试次数
            retry_count = 0
            success = False

            # 重试循环
            while retry_count <= max_retries and not success:
                try:
                    success = driver.execute_script("""
                        const index = arguments[0];

                        // 1. 查找build-status元素
                        const buildStatus = document.querySelector('body > build-status, body > * > build-status');
                        if (!buildStatus) return false;

                        // 2. 进入Shadow DOM
                        const shadowRoot = buildStatus.shadowRoot;
                        if (!shadowRoot) return false;

                        // 3. 查找.buildHistory容器
                        const buildHistory = shadowRoot.querySelector('div.buildHistory');
                        if (!buildHistory) return false;

                        // 4. 获取所有paper-button
                        const buttons = buildHistory.querySelectorAll('paper-button');
                        if (!buttons || index >= buttons.length) return false;

                        // 5. 点击按钮
                        buttons[index].click();
                        return true;
                    """, index)

                    if not success:
                        print(f"⚠️ 无法点击按钮 #{index}")
                        raise Exception("JavaScript点击操作失败")

                    print(f"✅ 按钮 #{index} 已点击 (尝试 {retry_count + 1}/{max_retries + 1})")
                    success = True  # 标记成功

                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ 尝试 #{retry_count + 1} 失败: {error_msg}")
                    # 检查是否是超时错误
                    if "Read timed out" in error_msg and retry_count < max_retries:
                        retry_count += 1
                        print(f"♻️ 将在 {2 ** retry_count} 秒后重试...")
                        time.sleep(2 ** retry_count)  # 指数退避等待
                    else:
                        print(f"🚫 按钮 #{index} 处理失败，已达到最大重试次数")
                        break  # 跳出重试循环

            if not success:
                print(f"⚠️ 无法点击按钮 #{index}，跳过")
                with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                    fi.write(url + "\n")
                continue

            # 等待日志加载
            print("⏳ 等待日志加载...")
            # 重新展平Shadow DOM获取新内容，最多3秒
            expand_shadow_dom_with_timeout(driver, 3)

            # 获取页面HTML
            page_html = driver.page_source

            # 提取日志文件URL
            log_url = None
            try:
                # 使用BeautifulSoup解析HTML
                soup = BeautifulSoup(page_html, 'html.parser')

                # 查找所有包含日志链接的<a>标签
                log_links = soup.find_all('a', href=True)

                # 筛选出包含日志的链接
                for link in log_links:
                    href = link.get('href', '')
                    if href.startswith('/log-') and href.endswith('.txt'):
                        log_url = f"https://oss-fuzz-build-logs.storage.googleapis.com{href}"
                        print(f"🔗 找到日志文件URL: {log_url}")
                        log_url_list.append(log_url)
                        date_and_state_list.append(date_part + " " + status_str)
                        break

                if not log_url:
                    print("⚠️ 未找到日志文件URL")
                    with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                        fi.write(url + "\n")

            except Exception as e:
                print(f"❌ 日志URL提取失败: {str(e)}")
                with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                    fi.write(url + "\n")

        except Exception as e:
            print(f"❌ 处理按钮 #{index} 时发生错误: {str(e)}")
            with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                fi.write(url + "\n")
            continue

        finally:
            if 'driver' in locals():
                driver.quit()
                print(f"🚪 按钮 #{index} 的浏览器已关闭")

    return log_url_list, date_and_state_list


def extract_between_markers(html: str) -> List[str]:
    """
    使用正则表达式从 html 文本中抽取所有外层 <div>…</div> 结构内的项目名，
    仅在该 <div> 内含有 icon="icons:error" 才匹配。
    对每个匹配结果，去掉可能残留的 '/dom-if>' 前缀，只保留真正的项目名。
    """
    pattern = re.compile(
        r'<iron-icon[^>]*icon=["\']icons:error["\'][\s\S]*?</iron-icon>'  # 包含 error 图标
        r'[\s\S]*?'  # 中间任意内容（shadow DOM、dom-if 等）
        r'([^<\s][^<]+?)\s*'  # 捕获非空白开头直到下一个 '<' 之间的文本
        r'</div>',  # 直到外层 </div>
        re.IGNORECASE
    )
    raw = pattern.findall(html)
    cleaned = []
    for m in raw:
        # m 里可能是 "/dom-if>\n                  zip-rs"
        # split by '>'，取最后一段，再 strip 掉前后空白
        name = m.split('>')[-1].strip()
        cleaned.append(name)
    return cleaned


def fetch_and_extract(chromedriver_path: str) -> List[str]:
    """
    启动 Chrome、展平 Shadow DOM、获取页面 HTML，
    并提取所有项目名称对应的url，最后以列表形式返回。
    """
    opts = Options()
    # opts.add_argument("--headless")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )

    service = Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(service=service, options=opts)
    try:
        driver.get("https://oss-fuzz-build-logs.storage.googleapis.com/index.html")
        WebDriverWait(driver, 100).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
        )
        time.sleep(20)

        # 展平所有 Shadow DOM
        expand_shadow_dom(driver)
        # 获取完整渲染后的 HTML
        rendered_html = driver.page_source

        # 提取并返回所有匹配的片段列表
        return extract_between_markers(rendered_html)

    finally:
        driver.quit()


def download_with_urllib(log_url, log_filename, project_name, step):
    """
    将目标 log 下载到本地
    参数依次是日志下载url列表，存储文件名列表，存储文件夹名称，重试次数
    """
    try:
        # 创建自定义上下文，忽略SSL验证
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        # 设置自定义 User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        }

        # 创建请求
        req = urllib.request.Request(log_url, headers=headers)

        print(f"⬇️ 开始下载日志 (urllib): {log_url}")
        with urllib.request.urlopen(req, context=context, timeout=50) as response:
            data = response.read()
            # 确保项目文件夹存在
            os.makedirs(project_name, exist_ok=True)
            # 构建完整的文件路径：项目文件夹 + 文件名
            full_path = os.path.join(project_name, log_filename)
            # 保存文件到项目文件夹
            with open(full_path, "wb") as log_file:
                log_file.write(data)
            # 打印完整的保存路径
            print(f"💾 日志已下载并保存到: {full_path}")
            print(f"📝 日志大小: {len(data)} 字符")
            return True

    except Exception as e:
        print(f"❌ 下载日志文件失败 (urllib): {str(e)}")
        if step < 3:
            print(f"✅ 下载日志文件重试 (urllib): {step + 1}/3")
            download_with_urllib(log_url, log_filename, project_name, step + 1)
        return False


def fetch_rendered_page_and_done(chromedriver_path, url, step):
    """
    对目标项目构建日志进行提取和下载
    """
    # 从URL中提取项目名称
    project_name = url.split("#")[-1] if "#" in url else "unknown_project"

    # 1. Chrome 配置
    opts = Options()
    # opts.add_argument("--headless")
    opts.add_argument("--enable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )
    opts.add_argument("--window-size=1200,900")

    driver = webdriver.Chrome(service=Service(chromedriver_path), options=opts)
    try:
        driver.get(url)
        print(f"🌐 访问URL: {url}")

        # 等待 build-status 出现并异步加载完毕
        WebDriverWait(driver, 100).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
        )
        print("✅ 主组件已加载")
        time.sleep(20)

        # 初始展开Shadow DOM
        expand_shadow_dom(driver)

        # 获取所有构建按钮
        buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "div.buildHistory paper-button"
        )
        if not buttons:
            if step < 3:
                print(f"✅重新进行按钮获取，尝试{step + 1}/3")
                fetch_rendered_page_and_done(chromedriver_path, url, step + 1)
            else:
                print(f"⚠️无 <paper-button> 元素，跳过")
                with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                    fi.write(url + "\n")
                return None

        print(f"🔍 找到 {len(buttons)} 个构建按钮")

        # 提取时间戳和按钮状态
        ts_pattern = re.compile(r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2}")
        timestamps = []
        for btn in buttons:
            m = ts_pattern.search(btn.text)
            timestamps.append(m.group() if m else "unknown_time")

        # 提取按钮状态 (1=成功, 0=失败)
        note = []
        for btn in buttons:
            outer_html = btn.get_attribute("outerHTML")
            if 'icon="icons:done"' in outer_html:
                note.append(1)  # 成功
            elif 'icon="icons:error"' in outer_html:
                note.append(0)  # 失败
            else:
                note.append(-1)  # 未知状态

        # 创建标记数组，过滤掉不需要的按钮
        mark = []
        # 标记该项目有多少需要获取的url
        number = 0
        for i in range(len(note)):
            # 只有一个按钮的情况下
            if len(note) == 1:
                mark.append(note[i])  # 保留原始状态
                number += 1
            elif i == 0 and note[i] == note[i + 1] and i + 1 < len(note):
                mark.append(3)  # 标记为不需要
            elif i == len(note) - 1 and note[i] == note[i - 1] and i - 1 >= 0:
                mark.append(3)  # 标记为不需要
            elif i - 1 >= 0 and i + 1 < len(note) and note[i] == note[i - 1] and note[i] == note[i + 1]:
                mark.append(3)  # 标记为不需要
            else:
                mark.append(note[i])  # 保留原始状态
                number += 1

        print(f"📊 构建状态统计: 成功={note.count(1)}, 失败={note.count(0)}, 未知={note.count(-1)}")
        driver.quit()
        if number != 0:
            # 对需要进行获取的log所在项目url进行统计
            with open("target_url_list.txt", "a", encoding="utf-8") as fi:
                fi.write(url + "\n")
            # 需要进行log的获取
            # 组合数据
            combined = [(i, timestamps[i], note[i]) for i in range(len(timestamps))]
            # 使用提取函数获取日志URL和日期状态
            log_url_list, date_and_state_list = extract_build_log_urls(chromedriver_path, url, combined, mark)
            # 统一对日志进行获取
            for i, log_url in enumerate(log_url_list):
                download_with_urllib(log_url, date_and_state_list[i], project_name, 0)

            print("✅ 所有构建日志处理完成")

        return {
            "project": project_name,
            "total_buttons": len(buttons),
            "processed": len([m for m in mark if m != 3])
        }

    except Exception as e:
        print(f"❌ 发生错误: {str(e)}")
        with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
            fi.write(url + "\n")
        driver.quit()
        return None

    finally:
        driver.quit()
        print("🚪 浏览器已关闭")


def main(chromedriver_path):
    """主函数"""
    # 创建日志文件名（包含时间戳）
    log_filename = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    # 使用Tee类重定向输出
    with Tee(log_filename) as tee:
        try:
            # 获取所有build失败的项目的URL
            snippets_list = fetch_and_extract(chromedriver_path)
            # 获取各个构件失败项目的URL
            print("抽取到的所有项目拼接url：")
            project_urls = []
            base_url = "https://oss-fuzz-build-logs.storage.googleapis.com/index.html#"
            for idx, snippet in enumerate(snippets_list, 1):
                project_urls.append(base_url + snippet)
                print(f"{idx}: {base_url + snippet}\n")
            with open("project_url_list.txt", "w", encoding="utf-8") as f:
                for url in project_urls:
                    f.write(url + "\n")
            print(f"✅ 已将 {len(project_urls)} 条 URL（保存到 project_url_list.txt")
            # 获取并下载日志到本地
            with open("project_url_list.txt", "r", encoding="utf-8") as fin:
                for line in fin:
                    url = line.strip()
                    if not url:
                        continue
                    result = fetch_rendered_page_and_done(chromedriver_path, url, 0)
                    if result:
                        print(f"🎉 项目 '{result['project']}' 处理完成")
                        print(f"  总按钮数: {result['total_buttons']}")
                        print(f"  处理按钮数: {result['processed']}")

            # 如果文件不存在，就创建一个空文件
            file_path = "wrong_url_list.txt"
            if not os.path.exists(file_path):
                open(file_path, "w", encoding="utf-8").close()
            with open("wrong_url_list.txt", "r", encoding="utf-8") as fin:
                for line in fin:
                    url = line.strip()
                    if not url:
                        continue
                    result = fetch_rendered_page_and_done(chromedriver_path, url, 0)
                    if result:
                        print(f"🎉 项目 '{result['project']}' 处理完成")
                        print(f"  总按钮数: {result['total_buttons']}")
                        print(f"  处理按钮数: {result['processed']}")
        except Exception as e:
            # 捕获并记录所有未处理异常.
            print(f"❌ 发生未处理的异常: {str(e)}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            raise  # 重新抛出异常以便在finally块中处理
        finally:
            # 确保所有输出都被刷新
            tee.flush()
            print("✅ 日志已保存到:", log_filename)


if __name__ == "__main__":
    # 获取当前脚本所在的目录,构建相对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    chromedriver_path = os.path.join(
        current_dir,
        "chromedriver",
        "chromedriver-win64",
        "chromedriver.exe"
    )
    try:
        main(chromedriver_path)
    except Exception as e:
        print("程序因异常退出，日志已保存")
        sys.exit(1)  # 非零退出码表示异常退出
