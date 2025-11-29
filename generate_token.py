import yaml
import sys
import secrets

def load_config() -> dict:
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print("config.yaml 不存在, 你可以直接运行主程序来生成 (Not Found, you can run the main program to generate it)")
        sys.exit(1)
    return config

def main():
    config = load_config()
    config["token"] = secrets.token_urlsafe(32)
    with open("config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"新的 token (New token): {config['token']}")

if __name__ == "__main__":
    main()