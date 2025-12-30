def duplicate_removal(input_filename='target_url_list.txt', output_filename='project_url_list.txt'):
    """
    读取输入文件，去除重复字符串，并将唯一字符串写入输出文件

    参数:
        input_filename: 输入文件名，默认为 'target_url_list.txt'
        output_filename: 输出文件名，默认为 'project_url_list.txt'
    """
    # 使用集合存储唯一字符串
    unique_strings = set()

    try:
        # 读取输入文件
        with open(input_filename, 'r', encoding='utf-8') as input_file:
            for line in input_file:
                # 去除行尾换行符和空白字符
                cleaned_line = line.strip()
                # 忽略空行
                if cleaned_line:
                    unique_strings.add(cleaned_line)

        # 写入输出文件
        with open(output_filename, 'a', encoding='utf-8') as output_file:
            for string in unique_strings:
                output_file.write(string + '\n')

        # 清空输入文件
        with open(input_filename, 'w', encoding='utf-8') as input_file:
            input_file.write('')

        print(f"成功处理完成！共找到 {len(unique_strings)} 个唯一字符串")
        print(f"原始文件: {input_filename} (已清空)")
        print(f"输出文件: {output_filename}")
        return len(unique_strings)

    except FileNotFoundError:
        print(f"错误: 文件 '{input_filename}' 未找到，请确保它在当前目录")
        return -1
    except Exception as e:
        print(f"处理过程中发生错误: {str(e)}")
        return -1


# 保留原有的直接执行功能
if __name__ == "__main__":
    duplicate_removal()