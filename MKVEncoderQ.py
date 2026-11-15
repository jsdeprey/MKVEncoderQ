import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare-videos"]:
        from video_optimizer.compare_player import main
        raise SystemExit(main(sys.argv[2:]))
    from video_optimizer.combined_gui import main as gui_main
    raise SystemExit(gui_main())
