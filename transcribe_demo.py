#!/usr/bin/env python

import argparse
import os
import numpy as np
import speech_recognition as sr
from lightning_whisper_mlx import LightningWhisperMLX

from datetime import datetime, timedelta
from queue import Queue
from time import sleep
from sys import platform


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default="medium",
        help="Model to use",
        choices=[
            "tiny",
            "small",
            "distil-small.en",
            "base",
            "medium",
            "distil-medium.en",
            "large",
            "large-v2",
            "distil-large-v2",
            "large-v3",
            "distil-large-v3",
        ],
    )
    parser.add_argument(
        "--energy_threshold",
        default=1000,
        help="Energy level for mic to detect.",
        type=int,
    )
    parser.add_argument(
        "--record_timeout",
        default=1,
        help="How real time the recording is in seconds.",
        type=float,
    )
    parser.add_argument(
        "--phrase_timeout",
        default=3,
        help="How much empty space between recordings before we "
        "consider it a new line in the transcription.",
        type=float,
    )
    parser.add_argument(
        "--output_file",
        help="File path to save the transcription.",
        type=str,
    )
    if "linux" in platform:
        parser.add_argument(
            "--default_microphone",
            default="pulse",
            help="Default microphone name for SpeechRecognition. "
            "Run this with 'list' to view available Microphones.",
            type=str,
        )
    args = parser.parse_args()

    # The last time a recording was retrieved from the queue.
    phrase_time = None
    # Thread safe Queue for passing data from the threaded recording callback.
    data_queue = Queue()
    # We use SpeechRecognizer to record our audio because it has a nice feature where it can detect when speech ends.
    recorder = sr.Recognizer()
    recorder.energy_threshold = args.energy_threshold
    # Definitely do this, dynamic energy compensation lowers the energy threshold dramatically to a point where the SpeechRecognizer never stops recording.
    recorder.dynamic_energy_threshold = False

    # Important for linux users.
    # Prevents permanent application hang and crash by using the wrong Microphone
    source = None
    if "linux" in platform:
        mic_name = args.default_microphone
        if not mic_name or mic_name == "list":
            print("Available microphone devices are: ")
            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                print(f'Microphone with name "{name}" found')
            return
        else:
            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                if mic_name in name:
                    source = sr.Microphone(sample_rate=16000, device_index=index)
                    break
    else:
        source = sr.Microphone(sample_rate=16000)

    # Load / Download model
    model = args.model
    audio_model = LightningWhisperMLX(model, batch_size=12, quant=None)

    record_timeout = args.record_timeout
    phrase_timeout = args.phrase_timeout

    transcription = [""]

    if source:
        with source:
            recorder.adjust_for_ambient_noise(source)

    def record_callback(_, audio: sr.AudioData) -> None:
        """
        Threaded callback function to receive audio data when recordings finish.
        audio: An AudioData containing the recorded bytes.
        """
        # Grab the raw bytes and push it into the thread safe queue.
        data = audio.get_raw_data()
        data_queue.put(data)

    # Create a background thread that will pass us raw audio bytes.
    # We could do this manually but SpeechRecognizer provides a nice helper.
    recorder.listen_in_background(
        source, record_callback, phrase_time_limit=record_timeout
    )

    # Cue the user that we're ready to go.
    print("Model loaded.\n")

    while True:
        try:
            now = datetime.utcnow()
            # Pull raw recorded audio from the queue.
            if not data_queue.empty():
                phrase_complete = False
                # If enough time has passed between recordings, consider the phrase complete.
                # Clear the current working audio buffer to start over with the new data.
                if phrase_time and now - phrase_time > timedelta(
                    seconds=phrase_timeout
                ):
                    phrase_complete = True
                # This is the last time we received new audio data from the queue.
                phrase_time = now

                # Combine audio data from queue
                audio_data = b"".join(data_queue.queue)
                data_queue.queue.clear()

                # Convert in-ram buffer to something the model can use directly without needing a temp file.
                # Convert data from 16 bit wide integers to floating point with a width of 32 bits.
                # Clamp the audio stream frequency to a PCM wavelength compatible default of 32768hz max.
                audio_np = (
                    np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )

                # Read the transcription.
                result = audio_model.transcribe(audio_np)
                text = result["text"].strip()

                # If we detected a pause between recordings, add a new item to our transcription.
                # Otherwise edit the existing one.
                if phrase_complete:
                    transcription.append(text)
                else:
                    transcription[-1] = text

                # Clear the console to reprint the updated transcription.
                # os.system("cls" if os.name == "nt" else "clear")
                for line in transcription:
                    print(line)
                # Write to file if output_file is specified
                if args.output_file:
                    with open(args.output_file, "w", encoding="utf-8") as f:
                        f.write("\n".join(transcription))
                # Flush stdout.
                # print("", end="", flush=True)
            else:
                # Infinite loops are bad for processors, must sleep.
                sleep(0.25)
        except KeyboardInterrupt:
            break

    print("\n\nTranscription:")
    for line in transcription:
        print(line)


if __name__ == "__main__":
    main()
