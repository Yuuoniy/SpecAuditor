
import uuid
import os
import re
from ASTParser import ASTParser
import subprocess
from collections import Counter
import json

class CodeSearcher:
    def __init__(self, source_dir):
        self.source_dir = source_dir
        self.weggli_path = 'weggli'
    
    def split_weggli_data(self, data):
        pattern = rf"{self.source_dir}.*\n"
        res = re.split(pattern,data)
        callers_of_main_api = []
        for i in range(len(res)):
            if len(res[i]) == 0:
                continue
            funcname = ASTParser.get_func_name_from_def(res[i])
            if funcname=='':
                continue
            callers_of_main_api.append(funcname)
        return callers_of_main_api

    def query_given_func_code(self,func_name):
        query =  f"'_ {func_name}(_){{}}'"
       
        data = self.query_code(query)
        
        # Extract file path from the data using regex
        file_path = ""
        pattern = rf"{self.source_dir}.*\n"
        matches = re.findall(pattern, data)
        if matches:
            file_path = matches[0].strip()
        
        func_code_dict = self.split_weggli_data_with_code(data)
        
        if func_name in func_code_dict:
            func_code = func_code_dict[func_name]
            func = func_code + '\n}'
            return func
        else:
            # If function not found, return empty
            return ""
    

    def query_given_func_usage(self, func_name, max_results=3):
        """
        Query usage examples of a given function and return top n results
        
        Args:
            func_name: Name of the function to search for
            max_results: Maximum number of usage examples to return
            
        Returns:
            List of code sections
        """

        
        query = f"'{func_name}();'"
        data = self.query_code(query)
        
        if not data:
            return []
        
        pattern = rf"{self.source_dir}.*\n"
        sections = re.split(pattern, data)
        
        usage_examples = []
        
        # Process each section to extract usage examples
        for section in sections:
            if len(section.strip()) == 0:
                continue
                
            usage_examples.append(section.strip())
                
            if len(usage_examples) >= max_results:
                break
        
        return usage_examples
    
    def split_weggli_data_with_code(self, data)->dict:
        func_code_dict = {}
        pattern = rf"{self.source_dir}.*\n"
        res = re.split(pattern,data)
        for func in res:
            if len(func) == 0:
                continue
            funcname = ASTParser.get_func_name_from_def(func)
            if funcname=='':
                continue
            func_code_dict[funcname] = func
        return func_code_dict


    def query_code(self, query):
        directory_path = ".code_query_tmp_results"
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)
        
        file = os.path.join(directory_path,uuid.uuid4().hex)

        cmd = f"weggli {query} {self.source_dir} -A 500 -B 500 -l > {file}"
        # print(f"    🔍 Executing weggli command: {cmd}")
        os.system(cmd)
        try:
            data= open(file).read()
            
        except Exception:
            data = ''
        
        os.system(f'rm {file}')
        
        return data
    
    
    def query_code_with_log_to_file(self,query, out_file):
        cmd = f"{self.weggli_path} '{query}' {self.source_dir} -s {out_file}"
        
        result = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
        res = result.stdout.read().decode().split('\n')[0]
        return len(res) > 0
    
    def weggli_get_found_with_code(self, query):
        data = self.query_code(query)
        func = self.split_weggli_data_with_code(data)
        return func

    def __weggli_get_found_func(self, query):
        data = self.query_code(query)
        func = self.split_weggli_data(data)
        return func

    def weggli_get_founc_callee(self,query):
        directory_path = ".code_query_tmp_results"
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)
        
        file = os.path.join(directory_path,uuid.uuid4().hex)
        self.query_code_with_log_to_file(query,file)
        callees = self.__parse_to_get_field(file,'callee')
        return callees
    
    def weggli_get_desired_filed(self,query,field):
        directory_path = ".code_query_tmp_results"
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)
        
        file = os.path.join(directory_path,uuid.uuid4().hex)
        self.query_code_with_log_to_file(query,file)
        vals = self.__parse_to_get_field(file,field)
        return vals
    
    
    def __parse_to_get_field(self,outfile,field):
        try:
            with open(outfile, 'r') as file:
                data = json.load(file)

            vals = []
            for file_set in data:
                for file_entry in file_set:
                    for match_group in file_entry['matches']:
                        for match in match_group['vars']:
                            if match['var'] == '$'+field:
                                vals.append(match['val'])
        except Exception as e:
            print(e)
            vals = []

        return vals
    
    
    def weggli_get_found_func(self, query):
        try:
            return list(set(self.__weggli_get_found_func(query)))
        except Exception as e:
            print(e)
            return []


def main():
    test_function = "kmalloc"
    source_dir = "./linux"
    
    searcher = CodeSearcher(source_dir)
    
    print(f"Testing {test_function}:")
    func_code, file_path = searcher.query_given_func_code(test_function)
    print(f"Code found: {len(func_code) > 0}")
    
    usage_examples = searcher.query_given_func_usage(test_function, 2)
    print(f"Usage examples found: {len(usage_examples)}")

    print("Function Code:")
    print(func_code)
    print(f"File Path: {file_path}")
    print("Usage Examples:")
    for example in usage_examples:
        print(example)
        print("-----")

    
if __name__ == "__main__":
    main()
    