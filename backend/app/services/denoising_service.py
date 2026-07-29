from app.denoising.temperature import denoise_temperature_points
from app.denoising.pressure import denoise_pressure_points
from app.denoising.motion import denoise_motion_points
from app.denoising.acoustic import denoise_acoustic_points

from app.database import get_sensor_history



def apply_denoising(data: dict) -> dict:

    event_type = data.get("event_type")
    device_id = data.get("device_id")


    history = get_sensor_history(
        device_id=device_id,
        event_type=event_type,
        limit=50
    )


    # add current point
    history.insert(0, data)



    # =========================
    # ENVIRONMENT
    # =========================

    if event_type == "environment":


        temperature_values = [
            x["temperature"]["value"]
            for x in history
        ]

        pressure_values = [
            x["pressure"]["value"]
            for x in history
        ]


        filtered_temperature = (
            denoise_temperature_points(
                temperature_values[::-1]
            )[-1]
        )


        filtered_pressure = (
            denoise_pressure_points(
                pressure_values[::-1]
            )[-1]
        )


        data["temperature"]["filtered_value"] = (
            float(filtered_temperature)
        )


        data["pressure"]["filtered_value"] = (
            float(filtered_pressure)
        )


        # no denoiser yet for humidity
        data["humidity"]["filtered_value"] = (
            data["humidity"]["value"]
        )



    # =========================
    # MOTION
    # =========================

    elif event_type == "motion":


        accel_x = [
            x["accelerometer"]["accel_x"]
            for x in history
        ]

        accel_y = [
            x["accelerometer"]["accel_y"]
            for x in history
        ]

        accel_z = [
            x["accelerometer"]["accel_z"]
            for x in history
        ]


        fx = denoise_motion_points(
            accel_x[::-1]
        )[-1]

        fy = denoise_motion_points(
            accel_y[::-1]
        )[-1]

        fz = denoise_motion_points(
            accel_z[::-1]
        )[-1]


        data["accelerometer"]["accel_x"] = float(fx)
        data["accelerometer"]["accel_y"] = float(fy)
        data["accelerometer"]["accel_z"] = float(fz)



    # =========================
    # ACOUSTIC
    # =========================

    elif event_type == "acoustic":


        sound_values = [
            x["sound"]["value"]
            for x in history
        ]


        filtered_sound = (
            denoise_acoustic_points(
                sound_values[::-1]
            )[-1]
        )


        data["sound"]["filtered_value"] = (
            float(filtered_sound)
        )


    return data