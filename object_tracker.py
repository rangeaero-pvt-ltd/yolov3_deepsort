import time

import cv2
import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
from absl import app, flags, logging
from absl.flags import FLAGS

from deep_sort import nn_matching, preprocessing
from deep_sort.detection import Detection
from deep_sort.track import TrackLocation
from deep_sort.tracker import Tracker
from tools import generate_detections as gdet
from yolov3_tf2.dataset import transform_images
from yolov3_tf2.models import YoloV3, YoloV3Tiny
from yolov3_tf2.utils import convert_boxes

flags.DEFINE_string('classes', './data/labels/coco.names',
                    'path to classes file')
flags.DEFINE_string('weights', './weights/yolov3-custom.tf',
                    'path to weights file')
flags.DEFINE_boolean('tiny', False, 'yolov3 or yolov3-tiny')
flags.DEFINE_integer('size', 416, 'resize images to')
flags.DEFINE_string('video', './data/video/roadcross.mp4',
                    'path to video file or number for webcam)')
flags.DEFINE_string('output', None, './data/video/result.mp4')
flags.DEFINE_string('output_format', 'XVID',
                    'codec used in VideoWriter when saving video to file')
flags.DEFINE_integer('num_classes', 1, 'number of classes in the model')


def isInsideRect(px, py, width, height, margin):
    return not (px < margin or px > width-margin or py < margin or py > height-margin)


def isInsideStrip(px, py, width, height, margin, strip_width):
    return not isInsideRect(px, py, width, height, margin+strip_width) and isInsideRect(px, py, width, height, margin)


def main(_argv):
    # Definition of the parameters
    max_cosine_distance = 0.5
    nn_budget = None
    nms_max_overlap = 0.8

    # initialize deep sort
    model_filename = 'model_data/mars-small128.pb'
    #model_filename = 'model_data/yolov3.weights'
    encoder = gdet.create_box_encoder(model_filename, batch_size=1)
    metric = nn_matching.NearestNeighborDistanceMetric(
        "cosine", max_cosine_distance, nn_budget)
    tracker = Tracker(metric)

    physical_devices = tf.config.experimental.list_physical_devices('GPU')
    if len(physical_devices) > 0:
        tf.config.experimental.set_memory_growth(physical_devices[0], True)

    if FLAGS.tiny:
        yolo = YoloV3Tiny(classes=FLAGS.num_classes)
    else:
        yolo = YoloV3(classes=FLAGS.num_classes)

    yolo.load_weights(FLAGS.weights)
    logging.info('weights loaded')

    class_names = [c.strip() for c in open(FLAGS.classes).readlines()]
    logging.info('classes loaded')

    try:
        vid = cv2.VideoCapture(int(FLAGS.video))
    except:
        vid = cv2.VideoCapture(FLAGS.video)

    out = None

    if FLAGS.output:
        # by default VideoCapture returns float instead of int
        width = int(vid.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(vid.get(cv2.CAP_PROP_FPS))
        codec = cv2.VideoWriter_fourcc(*FLAGS.output_format)
        out = cv2.VideoWriter(FLAGS.output, codec, fps, (width, height))
        list_file = open('detection.txt', 'w')
        frame_index = -1
    from collections import deque
    pts = [deque(maxlen=30) for _ in range(1000)]

    fps = 0.0
    count = 0
    frame_count = 0
    outflow_count = 0
    inflow_count = 0
    margin = 20
    outflow = set()
    inflow = set()
    while True:
        _, img = vid.read()

        if img is None:
            logging.warning("Empty Frame")
            time.sleep(0.1)
            count += 1
            if count < 3:
                continue
            else:
                break
        height, width, _ = img.shape
        img_in = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img_in = tf.expand_dims(img_in, 0)
        img_in = transform_images(img_in, FLAGS.size)

        t1 = time.time()
        boxes, scores, classes, nums = yolo.predict(img_in)
        classes = classes[0]
        names = []
        for i in range(len(classes)):
            names.append(class_names[int(classes[i])])
        names = np.array(names)
        converted_boxes = convert_boxes(img, boxes[0])
        features = encoder(img, converted_boxes)
        detections = [Detection(bbox, score, class_name, feature) for bbox, score,
                      class_name, feature in zip(converted_boxes, scores[0], names, features)]

        # initialize color map
        cmap = plt.get_cmap('tab20b')
        colors = [cmap(i)[:3] for i in np.linspace(0, 1, 20)]

        # run non-maxima suppresion
        boxs = np.array([d.tlwh for d in detections])
        scores = np.array([d.confidence for d in detections])
        classes = np.array([d.class_name for d in detections])
        indices = preprocessing.non_max_suppression(
            boxs, classes, nms_max_overlap, scores)
        detections = [detections[i] for i in indices]

        # Call the tracker
        tracker.predict()
        tracker.update(detections)
        frame_count += 1
        current_count = int(0)

        for track in tracker.tracks:
            if not track.is_confirmed() or track.time_since_update > 1:
                continue
            bbox = track.to_tlbr()
            class_name = track.get_class()
            color = colors[int(track.track_id) % len(colors)]
            color = [i * 255 for i in color]
            b = 1
            cx = int(0.5*(bbox[0]+bbox[2]))-b
            cy = int(0.5*(bbox[1]+bbox[3]))-b
            cv2.rectangle(img, (cx, cy), (cx+2*b, cy+2*b), color, 1)
            #cv2.rectangle(img, (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])), color, 2)
            #cv2.rectangle(img, (int(bbox[0]), int(bbox[1]-30)), (int(bbox[0])+(len(class_name)+len(str(track.track_id)))*17, int(bbox[1])), color, -1)
            #cv2.putText(img, class_name + "-" + str(track.track_id),(int(bbox[0]), int(bbox[1]-10)),0, 0.75, (255,255,255),2)
            #cv2.putText(img, str(track.track_id),(int(bbox[0]), int(bbox[1]-10)),0, 0.75, (255,255,255),2)
            center = (cx, cy)
            # pts[track.track_id].append(center)
            current_count += 1
            # for j in range(1, len(pts[track.track_id])):
            #     if pts[track.track_id][j-1] is None or pts[track.track_id][j] is None:
            #         continue
            #     thickness = int(np.sqrt(64/float(j+1))*2)
            #     cv2.line(img, (pts[track.track_id][j-1]),
            #              (pts[track.track_id][j]), color, thickness)

        # UNCOMMENT BELOW IF YOU WANT CONSTANTLY CHANGING YOLO DETECTIONS TO BE SHOWN ON SCREEN
        # for det in detections:
        #    bbox = det.to_tlbr()
        #    cv2.rectangle(img,(int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])),(255,0,0), 2)
        # print outflow count on screen

            if isInsideRect(cx, cy, width, height, margin):
                if(track.is_undefined()):
                    track.location = TrackLocation.In
                elif(track.is_inside()):
                    pass
                    # outflow.add(track.track_id)
                elif(track.is_outside()):
                    track.location = TrackLocation.Transient
                elif(track.is_transient()):
                    if(not isInsideStrip(cx, cy, width, height, margin, 10)):
                        inflow.add(track.track_id)
            else:
                if(track.is_undefined()):
                    track.location = TrackLocation.Out
                elif(track.is_inside()):
                    track.location = TrackLocation.Transient
                    # outflow.add(track.track_id)
                elif(track.is_outside()):
                    pass
                elif(track.is_transient()):
                    if(not isInsideStrip(cx, cy, width, height, margin, 10)):
                        outflow.add(track.track_id)

        if frame_count % 30 == 0:
            outflow_count = outflow.__len__()
            inflow_count = inflow.__len__()
            outflow.clear()
            inflow.clear()
        cv2.putText(img, "in: {}".format(inflow_count), (0, 120),
                    cv2.FONT_HERSHEY_COMPLEX_SMALL, 1, (0, 0, 255), 2)
        cv2.putText(img, "out: {}".format(outflow_count), (0, 90),
                    cv2.FONT_HERSHEY_COMPLEX_SMALL, 1, (0, 0, 255), 2)
        # print current count on screen
        cv2.putText(img, "count: {}".format(current_count), (0, 60),
                    cv2.FONT_HERSHEY_COMPLEX_SMALL, 1, (0, 0, 255), 2)
        # print fps on screen
        fps = (fps + (1./(time.time()-t1))) / 2
        cv2.putText(img, "FPS: {:.2f}".format(fps), (0, 30),
                    cv2.FONT_HERSHEY_COMPLEX_SMALL, 1, (0, 0, 255), 2)
        cv2.imshow('output', img)
        if FLAGS.output:
            out.write(img)
            frame_index = frame_index + 1
            list_file.write(str(frame_index)+' ')
            if len(converted_boxes) != 0:
                for i in range(0, len(converted_boxes)):
                    list_file.write(str(converted_boxes[i][0]) + ' '+str(converted_boxes[i][1]) + ' '+str(
                        converted_boxes[i][2]) + ' '+str(converted_boxes[i][3]) + ' ')
            list_file.write('\n')

        # press q to quit
        if cv2.waitKey(1) == ord('q'):
            break
    vid.release()
    if FLAGS.output:
        out.release()
        list_file.close()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    try:
        app.run(main)
    except SystemExit:
        pass
