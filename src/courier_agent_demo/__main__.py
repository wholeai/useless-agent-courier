from .app import create_app


def main() -> None:
    import uvicorn

    uvicorn.run("courier_agent_demo.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
