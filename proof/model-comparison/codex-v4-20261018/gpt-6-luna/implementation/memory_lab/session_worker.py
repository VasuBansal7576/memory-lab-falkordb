import json
from .sessions import process_pending
from .settings import open_memory,settings


def main():
    print(json.dumps(process_pending(open_memory(),settings()),indent=2))


if __name__=='__main__':main()
