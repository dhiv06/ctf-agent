"""Docker-free, API-free checks for the cryptography skill and its sandbox helpers (keyhunt, rsadec).

Keys are generated here in pure python (no openssl needed), so nothing depends on a particular challenge.
Run from the project root:  python tests/test_cryptography.py
"""
import base64
import hashlib
import math
import random
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "agent"))

import mock_tools  # noqa: E402

sys.modules["tools"] = mock_tools

import agent  # noqa: E402

FLAG = "picoCTF{unit_t3st_rsa_k3y}"
rng = random.Random(1234)


def is_prime(n):
    if n < 2 or n % 2 == 0:
        return n == 2
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(20):
        x = pow(rng.randrange(2, n - 1), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def prime(bits):
    while True:
        c = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if is_prime(c):
            return c


def tlv(tag, body):
    n = len(body)
    ln = bytes([n]) if n < 128 else (b"\x82" + n.to_bytes(2, "big") if n < 65536 else b"\x83" + n.to_bytes(3, "big"))
    return bytes([tag]) + ln + body


def der_int(v):
    return tlv(2, v.to_bytes((v.bit_length() + 8) // 8, "big"))


def make_key(bits=1024, e=65537):
    while True:
        p, q = prime(bits // 2), prime(bits // 2)
        if p != q and math.gcd(e, math.lcm(p - 1, q - 1)) == 1:
            break
    return p * q, e, pow(e, -1, math.lcm(p - 1, q - 1)), p, q


def pem(label, der):
    b = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64)) + f"\n-----END {label}-----\n"


def pkcs1_priv(n, e, d, p, q):
    vals = [0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p)]
    return tlv(0x30, b"".join(der_int(v) for v in vals))


ALG = tlv(0x30, bytes.fromhex("06092a864886f70d010101") + b"\x05\x00")


def pkcs8_priv(n, e, d, p, q):
    return tlv(0x30, der_int(0) + ALG + tlv(4, pkcs1_priv(n, e, d, p, q)))


def spki_pub(n, e):
    return tlv(0x30, ALG + tlv(3, b"\x00" + tlv(0x30, der_int(n) + der_int(e))))


def enc_raw(m, n, e):
    return pow(int.from_bytes(m, "big"), e, n).to_bytes((n.bit_length() + 7) // 8, "big")


def enc_pkcs1(m, n, e):
    k = (n.bit_length() + 7) // 8
    ps = bytes(rng.randrange(1, 256) for _ in range(k - len(m) - 3))
    return enc_raw(b"\x00\x02" + ps + b"\x00" + m, n, e)


def mgf1(seed, ln, h):
    out, c = b"", 0
    while len(out) < ln:
        out += h(seed + c.to_bytes(4, "big")).digest()
        c += 1
    return out[:ln]


def enc_oaep(m, n, e, h=hashlib.sha256):
    k, hl = (n.bit_length() + 7) // 8, h().digest_size
    db = h(b"").digest() + b"\x00" * (k - len(m) - 2 * hl - 2) + b"\x01" + m
    seed = bytes(rng.randrange(256) for _ in range(hl))
    mdb = bytes(a ^ b for a, b in zip(db, mgf1(seed, len(db), h)))
    mseed = bytes(a ^ b for a, b in zip(seed, mgf1(mdb, hl, h)))
    return enc_raw(b"\x00" + mseed + mdb, n, e)


def helper(name, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "sandbox" / name), *args],
                          capture_output=True, text=True, errors="replace")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    n, e, d, p, q = make_key()
    m = FLAG.encode()
    key8, key1, pub = tmp / "k8.pem", tmp / "k1.pem", tmp / "pub.pem"
    key8.write_text(pem("PRIVATE KEY", pkcs8_priv(n, e, d, p, q)))
    key1.write_text(pem("RSA PRIVATE KEY", pkcs1_priv(n, e, d, p, q)))
    pub.write_text(pem("PUBLIC KEY", spki_pub(n, e)))
    ct_pkcs1, ct_oaep, ct_raw = tmp / "c1.bin", tmp / "c2.bin", tmp / "c3.bin"
    ct_pkcs1.write_bytes(enc_pkcs1(m, n, e))
    ct_oaep.write_bytes(enc_oaep(m, n, e))
    ct_raw.write_bytes(enc_raw(m, n, e))
    ct_b64, ct_hex = tmp / "c1.b64", tmp / "c1.hex"
    ct_b64.write_text(base64.b64encode(ct_pkcs1.read_bytes()).decode() + "\n")
    ct_hex.write_text(ct_pkcs1.read_bytes().hex() + "\n")

    nums = tmp / "nums.txt"
    nums.write_text(f"n = {n}\ne = {e}\np = {p}\nq = {q}\nc = {int.from_bytes(ct_raw.read_bytes(), 'big')}\n")

    # public-key-only cases: tiny exponent with a short message, and close primes (Fermat)
    big = prime(512) * prime(512)
    small, small_ct = tmp / "small.txt", tmp / "small_ct.txt"
    small.write_text(f"n = {big}\ne = 3\n")
    small_ct.write_text(str(pow(int.from_bytes(m, "big"), 3, big)))
    pf = prime(256)
    qf = pf + 2
    while not is_prime(qf):
        qf += 2
    fer, fer_ct = tmp / "fermat.txt", tmp / "fermat_ct.bin"
    fer.write_text(f"n = {pf * qf}\ne = 65537\n")
    fer_ct.write_bytes(enc_pkcs1(m[:20], pf * qf, 65537))

    # carriers for keyhunt: image-like files hiding the key in different ways
    cover = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4 + b"\xff\xd9"
    keytext = key8.read_bytes()
    app, layered, hexed, clean, numfile = (tmp / x for x in ("appended.jpg", "layered.jpg", "hexed.png", "clean.jpg", "leak.txt"))
    app.write_bytes(cover + keytext)
    layered.write_bytes(cover + base64.b64encode(zlib.compress(keytext)))
    hexed.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(64) + b"IEND\xaeB`\x82" + keytext.hex().encode())
    clean.write_bytes(cover)
    numfile.write_text(f"d = {d}\nn = {n}\n")

    # hashcrack: a tiny throwaway wordlist containing a randomly generated password,
    # never written into the tool itself, plus candidates for each supported algorithm.
    secret_word = "zz" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(10))
    wordlist = tmp / "mini_wordlist.txt"
    wordlist.write_text("hello\nworld\n123456\n" + secret_word + "\nfoobar\n")

    def digest(algo, word):
        return getattr(hashlib, algo)(word.encode()).hexdigest()

    algos = ["md5", "sha1", "sha224", "sha256", "sha384", "sha512"]
    hash_files = {}
    for algo in algos:
        hf = tmp / f"{algo}.hash"
        hf.write_text(digest(algo, secret_word) + "\n")
        hash_files[algo] = hf

    def hashcrack(*args):
        return helper("hashcrack", *[str(a) for a in args])

    themed_word = "ThemeWord_" + secret_word
    themed_hash = digest("sha256", themed_word)

    def hunt(f):
        out = tmp / ("hunt_" + f.name)
        return helper("keyhunt", str(f), "-o", str(out)).stdout, out

    def solves(keypath, ct):
        return FLAG in helper("rsadec", str(keypath), str(ct)).stdout

    r_app, o_app = hunt(app)
    r_lay, o_lay = hunt(layered)
    r_hex, _ = hunt(hexed)
    r_clean, _ = hunt(clean)
    r_num, _ = hunt(numfile)
    found_app, found_lay = sorted(o_app.glob("*.pem")), sorted(o_lay.glob("*.pem"))
    cryptography = agent.build_system_prompt("general,cryptography")

    checks = {
        "cryptography prompt has cryptography + core, no git/forensics": "SKILL: CRYPTOGRAPHY" in cryptography and "SKILL: GIT" not in cryptography
            and "SKILL: FORENSICS" not in cryptography and "UNTRUSTED DATA" in cryptography,
        "cryptography skill documents keyhunt and rsadec": "- keyhunt " in cryptography and "- rsadec " in cryptography,
        "cryptography skill documents hashcrack": "- hashcrack " in cryptography,
        "default prompt unchanged": "SKILL: CRYPTOGRAPHY" not in agent.build_system_prompt(agent.DEFAULT_SKILLS),
        "rsadec: PKCS#8 key + PKCS#1 v1.5": solves(key8, ct_pkcs1),
        "rsadec: traditional PKCS#1 key + OAEP-sha256": solves(key1, ct_oaep),
        "rsadec: raw/textbook RSA": solves(key8, ct_raw),
        "rsadec: base64 ciphertext": solves(key8, ct_b64),
        "rsadec: hex ciphertext": solves(key8, ct_hex),
        "rsadec: n/e/p/q number file": solves(nums, nums),
        "rsadec: public key only, tiny message (e-th root)": solves(small, small_ct),
        "rsadec: public key only, close primes (Fermat)": FLAG[:20] in helper("rsadec", str(fer), str(fer_ct)).stdout,
        "rsadec: public key only, no weakness -> clean failure": helper("rsadec", str(pub), str(ct_pkcs1)).returncode != 0,
        "rsadec: non-key file -> clean error": "key error" in helper("rsadec", str(clean), str(ct_pkcs1)).stderr,
        "keyhunt: key appended after image end": bool(found_app) and "KEY BLOCK" in r_app and "appended data" in r_app,
        "keyhunt: key in base64+zlib layers": bool(found_lay) and "KEY BLOCK" in r_lay,
        "keyhunt: key as hex after PNG end": "KEY BLOCK" in r_hex,
        "keyhunt: clean image reports nothing": "KEY BLOCK" not in r_clean and "no key material found" in r_clean,
        "keyhunt: number list (d, n)": "number list" in r_num,
        "end to end: keyhunt output feeds rsadec": bool(found_app) and solves(found_app[0], ct_pkcs1),
        "end to end: layered key feeds rsadec": bool(found_lay) and solves(found_lay[0], ct_oaep),
        "hashcrack: cracks md5 from a wordlist": hashcrack(hash_files["md5"], wordlist).stdout.strip() == f"md5 {secret_word}",
        "hashcrack: cracks sha1 from a wordlist": hashcrack(hash_files["sha1"], wordlist).stdout.strip() == f"sha1 {secret_word}",
        "hashcrack: cracks sha224 from a wordlist": hashcrack(hash_files["sha224"], wordlist).stdout.strip() == f"sha224 {secret_word}",
        "hashcrack: cracks sha256 from a wordlist": hashcrack(hash_files["sha256"], wordlist).stdout.strip() == f"sha256 {secret_word}",
        "hashcrack: cracks sha384 from a wordlist": hashcrack(hash_files["sha384"], wordlist).stdout.strip() == f"sha384 {secret_word}",
        "hashcrack: cracks sha512 from a wordlist": hashcrack(hash_files["sha512"], wordlist).stdout.strip() == f"sha512 {secret_word}",
        "hashcrack: inline hex hash (not a file) also works": hashcrack(digest("md5", secret_word), wordlist).stdout.strip() == f"md5 {secret_word}",
        "hashcrack: password absent from wordlist -> NOT FOUND, exit 1": (
            lambda r: r.stdout.strip() == "NOT FOUND" and r.returncode == 1
        )(hashcrack(digest("md5", "not_in_any_list_" + secret_word), wordlist)),
        "hashcrack: --words supplies a theme word not in any generic wordlist": (
            hashcrack(themed_hash, wordlist, "--words", themed_word).stdout.strip() == f"sha256 {themed_word}"
        ),
        "hashcrack: --brute-digits cracks a short numeric PIN with no wordlist": (
            hashcrack(digest("md5", "0427"), "--brute-digits", "4").stdout.strip() == "md5 0427"
        ),
        "hashcrack: malformed hash -> clean error, exit 2": hashcrack("not_hex_at_all", wordlist).returncode == 2,
        "hashcrack: never hardcodes the password (helper source has no literal secret)": (
            secret_word not in (ROOT / "sandbox" / "hashcrack").read_text()
        ),
    }
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print("\nCRYPTOGRAPHY TEST PASSED" if all(checks.values()) else "\nCRYPTOGRAPHY TEST FAILED")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
