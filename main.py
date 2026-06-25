
# Take from a native google cloud storage and put into a in/ directory
from decode import decode
from encode import encode


def main():
    encode("test.probundle", "in", "Presentation")
    decode("test.probundle", "output", True)
    

if __name__ == "__main__":
    main()