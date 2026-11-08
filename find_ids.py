import re, sys

def main():
    text = open(sys.argv[1], encoding="utf-8").read()
    print(sorted(set(re.findall(r'id="(ContentPlaceHolder1_[^"]+)"', text))))

if __name__ == '__main__':
    main()